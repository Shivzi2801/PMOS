"""
backend/retrieval/retrieval_result.py

Result contract for the Retrieval Layer (S1.6).

This module defines the *output* half of the retrieval contract consumed by
downstream RAG systems (responsibility #10). The shape is deliberately stable
and self-describing so that orchestrators can build prompts and citations
without reaching back into the Indexing Layer.

A ``RetrievalResult`` is an ordered, paginated set of ``RetrievalHit`` objects
plus diagnostics (timing, expansion info, page metadata).

No external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class RetrievalHit:
    """
    A single retrieved chunk and everything a RAG layer needs to use it.

    Fields map onto the records produced by S1.5 (Indexing Layer):

    chunk_id / document_id / source come straight from the index;
    ``text`` is the chunk content; ``metadata`` is the chunk's metadata map;
    ``score`` is the *current* ranking score (similarity, then possibly
    overwritten by a reranker); ``vector_score`` preserves the original
    similarity so callers can audit reranking effects.
    """

    chunk_id: str
    document_id: str
    source: str
    text: str
    score: float
    tenant_id: str
    vector_score: Optional[float] = None
    rerank_score: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Provenance breadcrumbs for debugging / citations.
    highlights: Sequence[str] = field(default_factory=tuple)

    def with_score(
        self,
        score: float,
        *,
        rerank_score: Optional[float] = None,
    ) -> "RetrievalHit":
        """Return a copy with an updated score (used by rerankers)."""
        return replace(self, score=score, rerank_score=rerank_score)

    def to_dict(self) -> dict:
        """Serialize for transport to downstream systems."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source": self.source,
            "text": self.text,
            "score": self.score,
            "tenant_id": self.tenant_id,
            "vector_score": self.vector_score,
            "rerank_score": self.rerank_score,
            "metadata": dict(self.metadata),
            "highlights": list(self.highlights),
        }


@dataclass(frozen=True)
class PageInfo:
    """Pagination metadata returned alongside results."""

    offset: int
    limit: int
    returned: int
    total_candidates: int  # candidates after filtering, before pagination
    has_more: bool

    def to_dict(self) -> dict:
        return {
            "offset": self.offset,
            "limit": self.limit,
            "returned": self.returned,
            "total_candidates": self.total_candidates,
            "has_more": self.has_more,
        }


@dataclass(frozen=True)
class RetrievalDiagnostics:
    """Non-essential but valuable observability data attached to a result."""

    took_ms: float = 0.0
    expanded_terms: Sequence[str] = field(default_factory=tuple)
    reranked: bool = False
    candidates_fetched: int = 0
    candidates_after_acl: int = 0
    candidates_after_filters: int = 0
    degraded: bool = False  # True if a non-fatal fallback occurred
    notes: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "took_ms": self.took_ms,
            "expanded_terms": list(self.expanded_terms),
            "reranked": self.reranked,
            "candidates_fetched": self.candidates_fetched,
            "candidates_after_acl": self.candidates_after_acl,
            "candidates_after_filters": self.candidates_after_filters,
            "degraded": self.degraded,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class RetrievalResult:
    """
    The full retrieval response: ordered hits + pagination + diagnostics.

    This is the object every downstream RAG consumer should program against.
    """

    query_text: str
    tenant_id: str
    hits: Sequence[RetrievalHit]
    page: PageInfo
    diagnostics: RetrievalDiagnostics = field(default_factory=RetrievalDiagnostics)

    def __len__(self) -> int:
        return len(self.hits)

    def __iter__(self):
        return iter(self.hits)

    @property
    def is_empty(self) -> bool:
        return len(self.hits) == 0

    def texts(self) -> List[str]:
        """Convenience: the ordered chunk texts (typical RAG context build)."""
        return [h.text for h in self.hits]

    def to_dict(self) -> dict:
        return {
            "query_text": self.query_text,
            "tenant_id": self.tenant_id,
            "hits": [h.to_dict() for h in self.hits],
            "page": self.page.to_dict(),
            "diagnostics": self.diagnostics.to_dict(),
        }

    @staticmethod
    def empty(query_text: str, tenant_id: str, offset: int, limit: int) -> "RetrievalResult":
        return RetrievalResult(
            query_text=query_text,
            tenant_id=tenant_id,
            hits=(),
            page=PageInfo(
                offset=offset,
                limit=limit,
                returned=0,
                total_candidates=0,
                has_more=False,
            ),
        )
