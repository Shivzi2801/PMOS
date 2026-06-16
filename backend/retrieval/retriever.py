"""
backend/retrieval/retriever.py

Core retriever (S1.6).

This module owns the orchestration of a single retrieval call and, critically,
defines the **contract between the Retrieval Layer and the Indexing Layer
(S1.5)**: the ``VectorIndex`` Protocol and the ``IndexCandidate`` value object.
Anything that can satisfy ``VectorIndex`` (the real S1.5 store, an in-memory
fake, a remote shard) can be retrieved against without code changes.

Responsibilities covered here:
  #1 Semantic search        -- ``VectorIndex.search``
  #3 Ranking / reranking    -- via ``reranker`` (safe_rerank)
  #4 Source ACL filtering   -- via ``filters.apply_acl``
  #5 Tenant isolation       -- via ``filters.enforce_tenant_isolation`` (raises)
  #6 Query expansion hooks  -- via ``expander`` (safe_expand)
  #7 Metrics collection     -- via ``MetricsSink``
  #8 Error handling         -- index errors wrapped; non-fatal stages degrade
  #9 Pagination             -- applied after filtering + ranking
  #10 Downstream contract   -- returns ``RetrievalResult``

The end-to-end stage order (security-first, recall-then-precision):

    expand -> embed -> vector search -> tenant gate (raise) -> ACL drop
    -> metadata filter -> rerank -> paginate -> assemble result

No external dependencies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

from . import filters as filters_mod
from .errors import (
    IndexUnavailableError,
    RetrievalError,
    TenantIsolationError,
)
from .metrics import InMemoryMetrics, MetricsSink, Names, NullMetrics, RetrievalEvent
from .query_expansion import NoopExpander, QueryExpander, safe_expand
from .reranker import IdentityReranker, Reranker, safe_rerank
from .retrieval_query import RetrievalQuery
from .retrieval_result import (
    PageInfo,
    RetrievalDiagnostics,
    RetrievalHit,
    RetrievalResult,
)


# ---------------------------------------------------------------------------
# Indexing Layer contract (S1.5 <-> S1.6 boundary)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IndexCandidate:
    """
    Raw candidate as returned by the Indexing Layer's vector search.

    This is the *only* shape the retriever expects from S1.5. The Indexing
    Layer is responsible for tenant-scoping its search, but the retriever
    re-verifies (defence in depth). Fields mirror S1.5 chunk records.
    """

    chunk_id: str
    document_id: str
    source: str
    text: str
    tenant_id: str
    score: float  # similarity in [0, 1] (cosine) -- higher is better
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorIndex(Protocol):
    """
    The contract the Retrieval Layer requires of the Indexing Layer (S1.5).

    Implementations MUST:
      * scope results to ``tenant_id`` (mandatory),
      * return up to ``top_k`` candidates ordered by descending similarity,
      * raise an exception (any) on backend failure -- the retriever wraps it
        as ``IndexUnavailableError``.

    ``query_text`` is passed through so the index can own embedding; this keeps
    the embedding model on the indexing side (consistent with S1.5) and avoids
    a second embedding dependency in retrieval.
    """

    def search(
        self,
        *,
        query_text: str,
        tenant_id: str,
        top_k: int,
    ) -> Sequence[IndexCandidate]:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------
def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _to_hit(c: IndexCandidate) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=c.chunk_id,
        document_id=c.document_id,
        source=c.source,
        text=c.text,
        score=c.score,
        vector_score=c.score,
        tenant_id=c.tenant_id,
        metadata=c.metadata,
    )


@dataclass
class Retriever:
    """
    Semantic retriever over a single ``VectorIndex``.

    Parameters
    ----------
    index:
        Anything satisfying the ``VectorIndex`` contract.
    reranker:
        Strategy for reordering filtered candidates. Defaults to identity.
    expander:
        Query-expansion strategy. Defaults to no-op.
    metrics:
        Metrics sink. Defaults to NullMetrics (zero overhead).
    """

    index: VectorIndex
    reranker: Reranker = field(default_factory=IdentityReranker)
    expander: QueryExpander = field(default_factory=NoopExpander)
    metrics: MetricsSink = field(default_factory=NullMetrics)

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Execute a single retrieval call end-to-end."""
        start = _now_ms()
        notes: List[str] = []
        degraded = False
        self.metrics.incr(Names.QUERIES, tags={"tenant": query.tenant_id})

        # ---- Stage 1: query expansion (optional, never fatal) -------------
        expanded_terms: tuple = ()
        effective_query = query
        if query.enable_query_expansion:
            exp = safe_expand(self.expander, query)
            effective_query = exp.query
            expanded_terms = exp.expanded_terms
            if exp.degraded:
                degraded = True
                notes.append("query_expansion_degraded")
            if expanded_terms:
                self.metrics.incr(Names.EXPANDED, tags={"tenant": query.tenant_id})

        # ---- Stage 2: vector search (fatal on failure) --------------------
        try:
            raw = self.index.search(
                query_text=effective_query.text,
                tenant_id=query.tenant_id,
                top_k=query.top_k,
            )
        except Exception as exc:
            self._emit_failure(query, start, IndexUnavailableError.code)
            raise IndexUnavailableError(
                "vector index search failed",
                details={"tenant": query.tenant_id, "cause": type(exc).__name__},
            ) from exc

        candidates_fetched = len(raw)
        self.metrics.incr(
            Names.CANDIDATES, candidates_fetched, tags={"tenant": query.tenant_id}
        )
        hits = [_to_hit(c) for c in raw]

        # Optional similarity floor.
        if query.min_score is not None:
            hits = [h for h in hits if h.score >= query.min_score]

        # ---- Stage 3: security-first filtering ----------------------------
        # Tenant isolation RAISES on any foreign-tenant record.
        try:
            filtered, fstats = filters_mod.apply_all(
                hits,
                tenant_id=query.tenant_id,
                principals=query.acl_principals,
                clause=query.filters,
            )
        except TenantIsolationError:
            self._emit_failure(query, start, TenantIsolationError.code)
            raise

        acl_dropped = fstats.after_tenant - fstats.after_acl
        filter_dropped = fstats.after_acl - fstats.after_metadata
        if acl_dropped:
            self.metrics.incr(
                Names.ACL_DROPPED, acl_dropped, tags={"tenant": query.tenant_id}
            )
        if filter_dropped:
            self.metrics.incr(
                Names.FILTER_DROPPED, filter_dropped, tags={"tenant": query.tenant_id}
            )

        # ---- Stage 4: reranking (optional, never fatal) -------------------
        reranked = False
        if query.enable_rerank and filtered:
            ranked, rr_degraded = safe_rerank(self.reranker, effective_query, filtered)
            reranked = not rr_degraded and not isinstance(
                self.reranker, IdentityReranker
            )
            if rr_degraded:
                degraded = True
                notes.append("rerank_degraded")
            else:
                filtered = ranked
                if reranked:
                    self.metrics.incr(
                        Names.RERANKED, tags={"tenant": query.tenant_id}
                    )
        # else: preserve vector-similarity order

        total_candidates = len(filtered)

        # ---- Stage 5: pagination ------------------------------------------
        page = query.pagination
        window = filtered[page.offset : page.end]
        has_more = total_candidates > page.end

        took = _now_ms() - start

        # ---- Stage 6: assemble + metrics ----------------------------------
        diagnostics = RetrievalDiagnostics(
            took_ms=took,
            expanded_terms=expanded_terms,
            reranked=reranked,
            candidates_fetched=candidates_fetched,
            candidates_after_acl=fstats.after_acl,
            candidates_after_filters=fstats.after_metadata,
            degraded=degraded,
            notes=tuple(notes),
        )
        result = RetrievalResult(
            query_text=query.text,
            tenant_id=query.tenant_id,
            hits=tuple(window),
            page=PageInfo(
                offset=page.offset,
                limit=page.limit,
                returned=len(window),
                total_candidates=total_candidates,
                has_more=has_more,
            ),
            diagnostics=diagnostics,
        )

        self._emit_success(query, result)
        return result

    # -- metrics helpers ----------------------------------------------------
    def _emit_success(self, query: RetrievalQuery, result: RetrievalResult) -> None:
        d = result.diagnostics
        self.metrics.incr(Names.SUCCESS, tags={"tenant": query.tenant_id})
        self.metrics.timing(Names.LATENCY, d.took_ms, tags={"tenant": query.tenant_id})
        self.metrics.incr(
            Names.RETURNED, result.page.returned, tags={"tenant": query.tenant_id}
        )
        if result.is_empty:
            self.metrics.incr(Names.EMPTY, tags={"tenant": query.tenant_id})
        if d.degraded:
            self.metrics.incr(Names.DEGRADED, tags={"tenant": query.tenant_id})
        self.metrics.record_event(
            RetrievalEvent(
                tenant_id=query.tenant_id,
                query_fingerprint=str(hash(query.fingerprint())),
                success=True,
                took_ms=d.took_ms,
                candidates_fetched=d.candidates_fetched,
                returned=result.page.returned,
                reranked=d.reranked,
                expanded=bool(d.expanded_terms),
                degraded=d.degraded,
            )
        )

    def _emit_failure(
        self, query: RetrievalQuery, start_ms: float, error_code: str
    ) -> None:
        took = _now_ms() - start_ms
        self.metrics.incr(Names.FAILURE, tags={"tenant": query.tenant_id})
        self.metrics.timing(Names.LATENCY, took, tags={"tenant": query.tenant_id})
        self.metrics.record_event(
            RetrievalEvent(
                tenant_id=query.tenant_id,
                query_fingerprint=str(hash(query.fingerprint())),
                success=False,
                took_ms=took,
                error_code=error_code,
            )
        )
