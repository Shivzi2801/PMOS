"""
PMOS S1.5 — Index Fan-Out
chunk_strategy.py

Chunk sizing + overlap strategy.

Sizing is expressed in *characters* in this slice (token-aware sizing is a
future concern and is intentionally left as a pluggable measure). The strategy
is a pure, side-effect-free policy object: given a piece of normalized text it
yields (start, end) spans. The chunker (chunker.py) turns spans into chunks and
owns identity/metadata.

Two cooperating policies:

  * SizingStrategy  — how big a window is.
  * OverlapStrategy — how much consecutive windows share.

Overlap exists so that a fact spanning a boundary is not severed; the retrieval
layer can dedupe overlapping hits. Overlap is bounded to be strictly less than
size to guarantee forward progress (otherwise chunking would not terminate).

Boundary preference: we prefer to cut on a "soft" boundary (paragraph, then
sentence, then whitespace) within a lookback window, falling back to a hard cut
so that no chunk exceeds max_size. This keeps chunks semantically coherent
without an external tokenizer.
"""

from __future__ import annotations

import dataclasses
from typing import Iterator, List, Tuple

from .errors import ChunkingError

Span = Tuple[int, int]  # (start, end) half-open over the source string


@dataclasses.dataclass(frozen=True)
class ChunkSizing:
    """
    target_size : preferred chunk length in characters
    max_size    : hard ceiling; a chunk may never exceed this
    min_size    : chunks smaller than this are merged into the previous chunk
                  (avoids a dangling sliver at the tail)
    """

    target_size: int = 1200
    max_size: int = 1600
    min_size: int = 200

    def __post_init__(self) -> None:
        if not (0 < self.min_size <= self.target_size <= self.max_size):
            raise ChunkingError(
                "invalid sizing: require 0 < min_size <= target_size <= max_size"
            )


@dataclasses.dataclass(frozen=True)
class ChunkOverlap:
    """
    overlap : number of trailing characters of chunk N reused as the leading
              characters of chunk N+1. Must be < target_size for progress.
    """

    overlap: int = 150

    def validate_against(self, sizing: ChunkSizing) -> None:
        if self.overlap < 0:
            raise ChunkingError("overlap must be >= 0")
        if self.overlap >= sizing.target_size:
            raise ChunkingError("overlap must be < target_size")


# Soft boundary markers, in descending preference.
_BOUNDARY_SEQUENCES = ("\n\n", "\n", ". ", "; ", ", ", " ")


def _find_soft_boundary(text: str, hard_end: int, lookback: int) -> int:
    """
    Search backwards from hard_end for the latest soft boundary within
    `lookback` chars. Returns the cut index (end-exclusive). Falls back to
    hard_end when no boundary is found.
    """
    window_start = max(0, hard_end - lookback)
    window = text[window_start:hard_end]
    best = -1
    for sep in _BOUNDARY_SEQUENCES:
        idx = window.rfind(sep)
        if idx != -1:
            # cut after the separator
            candidate = window_start + idx + len(sep)
            if candidate > best:
                best = candidate
        if best != -1:
            break
    return best if best != -1 else hard_end


class ChunkPlanner:
    """Produces spans for a given text under a sizing+overlap policy."""

    def __init__(self, sizing: ChunkSizing, overlap: ChunkOverlap) -> None:
        overlap.validate_against(sizing)
        self.sizing = sizing
        self.overlap = overlap

    def plan(self, text: str) -> List[Span]:
        if text is None:
            raise ChunkingError("text must not be None")
        n = len(text)
        if n == 0:
            return []

        spans: List[Span] = []
        cursor = 0
        lookback = max(1, self.sizing.target_size - self.sizing.min_size)

        while cursor < n:
            hard_end = min(cursor + self.sizing.target_size, n)
            if hard_end < n:
                end = _find_soft_boundary(text, hard_end, lookback)
                # never exceed max_size; never go below cursor
                end = min(max(end, cursor + 1), cursor + self.sizing.max_size, n)
            else:
                end = n

            spans.append((cursor, end))

            if end >= n:
                break

            # advance with overlap, guaranteeing forward progress
            next_cursor = end - self.overlap.overlap
            if next_cursor <= cursor:
                next_cursor = end
            cursor = next_cursor

        return self._merge_tail_sliver(spans, n)

    def _merge_tail_sliver(self, spans: List[Span], n: int) -> List[Span]:
        """Fold a too-small final span into its predecessor."""
        if len(spans) < 2:
            return spans
        last_start, last_end = spans[-1]
        if (last_end - last_start) < self.sizing.min_size:
            prev_start, _ = spans[-2]
            merged_end = last_end
            # merged span must still respect max_size; if it would overflow,
            # leave the sliver as its own chunk rather than violate the ceiling.
            if (merged_end - prev_start) <= self.sizing.max_size:
                spans[-2] = (prev_start, merged_end)
                spans.pop()
        return spans

    def iter_spans(self, text: str) -> Iterator[Span]:
        yield from self.plan(text)


DEFAULT_SIZING = ChunkSizing()
DEFAULT_OVERLAP = ChunkOverlap()


def default_planner() -> ChunkPlanner:
    return ChunkPlanner(DEFAULT_SIZING, DEFAULT_OVERLAP)
