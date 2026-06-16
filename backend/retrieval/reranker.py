"""
backend/retrieval/reranker.py

Result ranking and reranking (S1.6, responsibility #3).

Pipeline-wise, ranking happens in two phases:

1. The vector store returns candidates ordered by raw similarity.
2. An optional ``Reranker`` reorders the surviving (filtered) candidates using
   a richer signal. This module ships dependency-free rerankers and a Protocol
   that an LLM-based reranker can implement later without touching the
   retriever (the requested extension point).

Rerankers operate on the *filtered* candidate list (tenant/ACL/metadata already
applied) so they never see records the caller may not view -- important if a
future LLM reranker forwards text to an external model.

Failures are isolated: ``safe_rerank`` falls back to the input order and flags
degradation rather than failing retrieval.

No external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol, Sequence, runtime_checkable

from .errors import RerankError
from .retrieval_query import RetrievalQuery
from .retrieval_result import RetrievalHit


@runtime_checkable
class Reranker(Protocol):
    """Extension point for reranking strategies (incl. future LLM rerankers)."""

    def rerank(
        self, query: RetrievalQuery, hits: Sequence[RetrievalHit]
    ) -> List[RetrievalHit]:  # pragma: no cover - protocol
        ...


class IdentityReranker:
    """No-op reranker: preserves vector-similarity order."""

    def rerank(
        self, query: RetrievalQuery, hits: Sequence[RetrievalHit]
    ) -> List[RetrievalHit]:
        return list(hits)


@dataclass
class LexicalOverlapReranker:
    """
    A transparent, deterministic reranker for tests and lightweight use.

    Blends the existing vector score with a lexical-overlap signal: the
    fraction of distinct query tokens that appear in the chunk text. The final
    score is::

        score = alpha * vector_score + (1 - alpha) * lexical_overlap

    Both inputs are expected in [0, 1]; vector scores are min-max normalized
    across the candidate set first so the blend is well-behaved.
    """

    alpha: float = 0.7

    def rerank(
        self, query: RetrievalQuery, hits: Sequence[RetrievalHit]
    ) -> List[RetrievalHit]:
        if not hits:
            return []
        if not (0.0 <= self.alpha <= 1.0):
            raise RerankError(
                "alpha must be in [0, 1]", details={"alpha": self.alpha}
            )

        q_tokens = {t.lower() for t in query.text.split() if t}
        scores = [h.vector_score if h.vector_score is not None else h.score for h in hits]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0

        reranked: List[RetrievalHit] = []
        for hit, raw in zip(hits, scores):
            norm_vec = (raw - lo) / span
            if q_tokens:
                doc_tokens = {t.lower() for t in hit.text.split() if t}
                overlap = len(q_tokens & doc_tokens) / len(q_tokens)
            else:
                overlap = 0.0
            blended = self.alpha * norm_vec + (1.0 - self.alpha) * overlap
            reranked.append(hit.with_score(blended, rerank_score=blended))

        reranked.sort(key=lambda h: h.score, reverse=True)
        return reranked


def safe_rerank(
    reranker: Reranker, query: RetrievalQuery, hits: Sequence[RetrievalHit]
) -> tuple[List[RetrievalHit], bool]:
    """
    Rerank defensively.

    Returns ``(hits, degraded)``. On failure, returns the original order with
    ``degraded=True`` so retrieval still succeeds (graceful degradation).
    """
    try:
        return reranker.rerank(query, hits), False
    except RerankError:
        return list(hits), True
    except Exception:  # defensive: a broken reranker must not break retrieval
        return list(hits), True
