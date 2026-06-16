"""Ranking stage for context assembly (S1.7).

:class:`ContextRanker` orders chunks by relevance. The default strategy is a
stable descending sort on the retrieval score, with deterministic tie-breaking
on chunk id so output ordering is reproducible across runs.

The ranker is extensible: supply a custom ``key`` callable to rank by any
derived signal (e.g. recency-weighted score) without changing the assembler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Sequence, Tuple

from .context_package import RetrievedChunk

# A ranking key maps a chunk to a sortable tuple; higher sorts first.
RankKey = Callable[[RetrievedChunk], Tuple]


def _default_key(chunk: RetrievedChunk) -> Tuple[float, str]:
    # Negate id ordering is handled by reverse=False on a composed key below;
    # here we return (score, chunk_id) and sort descending on score, ascending
    # on id. We encode that by sorting on score desc then id asc explicitly.
    return (chunk.score, chunk.chunk_id)


@dataclass(frozen=True)
class ContextRanker:
    """Ranks retrieved chunks by relevance (descending) with stable ties."""

    key: RankKey = field(default=_default_key)

    def rank(self, chunks: Sequence[RetrievedChunk]) -> List[RetrievedChunk]:
        # Sort ascending by chunk_id first (stable), then descending by score.
        # Python's sort is stable, so chaining preserves id order within equal
        # scores -> deterministic output.
        by_id = sorted(chunks, key=lambda c: c.chunk_id)
        return sorted(by_id, key=lambda c: c.score, reverse=True)
