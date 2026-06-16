"""
backend/retrieval/hybrid_retriever.py

Hybrid retrieval (S1.6, responsibility #2).

The base ``Retriever`` already applies metadata filters as a post-search step.
The ``HybridRetriever`` extends this with two additional hybrid behaviours that
matter at scale and for quality:

1. **Filter-aware over-fetch.** When restrictive metadata filters are present,
   a small ``top_k`` can be fully consumed by the filter, returning too few
   hits. The hybrid retriever inflates the candidate pool by an
   ``overfetch_factor`` so a full page survives filtering.

2. **Score fusion (vector + keyword/metadata signal).** A pluggable
   ``KeywordIndex`` (optional) contributes a lexical score that is fused with
   the vector score via Reciprocal Rank Fusion (RRF). This is the classic
   "hybrid search" pattern and is dependency-free here. When no keyword index
   is supplied, the hybrid retriever degrades cleanly to pure vector retrieval
   plus filter-aware over-fetch.

The hybrid retriever reuses the base retriever's filtering, ACL, tenant
isolation, reranking, pagination and metrics so behaviour stays consistent.

No external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

from .metrics import MetricsSink, NullMetrics
from .query_expansion import NoopExpander, QueryExpander
from .reranker import IdentityReranker, Reranker
from .retrieval_query import RetrievalQuery
from .retrieval_result import RetrievalResult
from .retriever import IndexCandidate, Retriever, VectorIndex


@runtime_checkable
class KeywordIndex(Protocol):
    """
    Optional lexical/keyword index contract for hybrid search.

    Returns chunk_id -> relevance-rank pairs (rank 0 == most relevant) for the
    given query, tenant-scoped. Implementations might wrap BM25, a SQL LIKE
    scan, or a trigram index. Kept minimal to avoid external dependencies.
    """

    def search_ranked(
        self, *, query_text: str, tenant_id: str, top_k: int
    ) -> Sequence[str]:  # pragma: no cover - protocol
        """Return chunk_ids ordered most- to least- relevant."""
        ...


def _rrf(rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion contribution for a 0-based rank."""
    return 1.0 / (k + rank + 1)


@dataclass
class HybridRetriever:
    """
    Vector + metadata/keyword hybrid retriever.

    Composes a base ``Retriever`` for the vector path, filtering, ACL, tenant
    isolation, reranking and pagination, and layers hybrid fusion on top.

    Parameters
    ----------
    index:
        Vector index (S1.5 contract).
    keyword_index:
        Optional lexical index for score fusion. If ``None``, pure-vector.
    overfetch_factor:
        Multiplier applied to ``top_k`` before filtering, to counteract
        filter shrinkage. Clamped to a sane maximum.
    rrf_k:
        RRF smoothing constant.
    """

    index: VectorIndex
    keyword_index: Optional[KeywordIndex] = None
    reranker: Reranker = field(default_factory=IdentityReranker)
    expander: QueryExpander = field(default_factory=NoopExpander)
    metrics: MetricsSink = field(default_factory=NullMetrics)
    overfetch_factor: int = 3
    max_overfetch: int = 500
    rrf_k: int = 60

    def __post_init__(self) -> None:
        # A fusing vector index wraps the underlying index when keyword search
        # is available, injecting fused scores before the base pipeline runs.
        self._base = Retriever(
            index=_FusingIndex(
                vector_index=self.index,
                keyword_index=self.keyword_index,
                rrf_k=self.rrf_k,
            )
            if self.keyword_index is not None
            else self.index,
            reranker=self.reranker,
            expander=self.expander,
            metrics=self.metrics,
        )

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        effective = self._apply_overfetch(query)
        return self._base.retrieve(effective)

    # -- internals ----------------------------------------------------------
    def _apply_overfetch(self, query: RetrievalQuery) -> RetrievalQuery:
        """Inflate top_k when metadata filters are present to avoid starvation."""
        from .retrieval_query import MATCH_ALL

        if query.filters is MATCH_ALL or not query.filters.children:
            return query
        factor = max(1, self.overfetch_factor)
        inflated = min(query.top_k * factor, self.max_overfetch)
        if inflated <= query.top_k:
            return query
        return replace(query, top_k=inflated)


@dataclass
class _FusingIndex:
    """
    Internal adapter that presents a ``VectorIndex`` whose candidate scores are
    fused (vector RRF + keyword RRF) before reaching the base retriever.

    Keeping fusion behind the ``VectorIndex`` contract means the base
    ``Retriever`` -- and all of its tenant/ACL/filter/rerank logic -- is reused
    verbatim.
    """

    vector_index: VectorIndex
    keyword_index: KeywordIndex
    rrf_k: int = 60

    def search(
        self, *, query_text: str, tenant_id: str, top_k: int
    ) -> Sequence[IndexCandidate]:
        vec = list(
            self.vector_index.search(
                query_text=query_text, tenant_id=tenant_id, top_k=top_k
            )
        )
        kw_ids = list(
            self.keyword_index.search_ranked(
                query_text=query_text, tenant_id=tenant_id, top_k=top_k
            )
        )

        vec_rank = {c.chunk_id: i for i, c in enumerate(vec)}
        kw_rank = {cid: i for i, cid in enumerate(kw_ids)}

        by_id: Dict[str, IndexCandidate] = {c.chunk_id: c for c in vec}
        fused: List[IndexCandidate] = []
        for cid, cand in by_id.items():
            score = _rrf(vec_rank[cid], self.rrf_k)
            if cid in kw_rank:
                score += _rrf(kw_rank[cid], self.rrf_k)
            fused.append(replace(cand, score=score))

        # Keyword-only hits that the vector index didn't return are skipped:
        # the retriever needs full chunk payloads, which only the vector index
        # provides here. This keeps the adapter dependency-free; a production
        # build can hydrate keyword-only ids from S1.5 by chunk_id.
        fused.sort(key=lambda c: c.score, reverse=True)
        return fused
