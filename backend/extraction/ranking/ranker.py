"""Ranking layer: deduplication and merge of extracted atoms.

The ``AtomRanker`` collapses semantically equivalent atoms (same dedup key)
into a single representative, keeping the highest-confidence instance and
merging provenance (source offsets) from all duplicates. Atom ordering in the
result is deterministic: by descending confidence, then by dedup key for
stable tie-breaking.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..contracts.atoms import Atom, SourceOffset


class AtomRanker:
    """Removes duplicates and merges equivalent atoms."""

    def rank(self, atoms: List[Atom]) -> List[Atom]:
        if not atoms:
            return []

        winners: Dict[Tuple, Atom] = {}

        for atom in atoms:
            key = atom.dedup_key()
            existing = winners.get(key)
            if existing is None:
                winners[key] = atom
                continue
            winners[key] = self._merge(existing, atom)

        ranked = list(winners.values())
        ranked.sort(key=lambda a: (-a.confidence, a.dedup_key()))
        return ranked

    def _merge(self, keep: Atom, other: Atom) -> Atom:
        """Merge two equivalent atoms.

        The higher-confidence atom is retained as the representative; source
        offsets from both are unioned so provenance is not lost.
        """
        primary, secondary = (keep, other) if keep.confidence >= other.confidence else (other, keep)
        primary.sourceOffsets = self._union_offsets(
            primary.sourceOffsets, secondary.sourceOffsets
        )
        return primary

    @staticmethod
    def _union_offsets(
        a: Tuple[SourceOffset, ...], b: Tuple[SourceOffset, ...]
    ) -> Tuple[SourceOffset, ...]:
        seen = {}
        for off in list(a) + list(b):
            seen[off.as_tuple()] = off
        ordered = sorted(seen.values(), key=lambda o: (o.start, o.end))
        return tuple(ordered)
