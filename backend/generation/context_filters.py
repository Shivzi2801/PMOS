"""Filtering stages for context assembly (S1.7).

Two pure, side-effect-free filters:

* :class:`DeduplicationFilter` — removes duplicate chunks. Duplicates are
  detected by chunk id first, then by a normalized content hash so that two
  different ids carrying identical text collapse to one.
* :class:`ACLFilter` — drops chunks the requesting principal is not permitted
  to see, based on access-control tags.

Both expose a uniform ``apply`` returning the kept chunks plus the ids dropped,
so the assembler can account for every chunk.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Set, Tuple

from .context_package import RetrievedChunk


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _content_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeduplicationFilter:
    """Removes duplicate chunks by id and by normalized-content hash.

    The first occurrence (in input order) of each unique chunk is kept; later
    duplicates are dropped.
    """

    def apply(
        self, chunks: Sequence[RetrievedChunk]
    ) -> Tuple[List[RetrievedChunk], List[str]]:
        seen_ids: Set[str] = set()
        seen_hashes: Set[str] = set()
        kept: List[RetrievedChunk] = []
        dropped: List[str] = []
        for chunk in chunks:
            chash = _content_hash(chunk.content)
            if chunk.chunk_id in seen_ids or chash in seen_hashes:
                dropped.append(chunk.chunk_id)
                continue
            seen_ids.add(chunk.chunk_id)
            seen_hashes.add(chash)
            kept.append(chunk)
        return kept, dropped


@dataclass(frozen=True)
class ACLFilter:
    """Drops chunks not visible to the principal's granted tags.

    A chunk is visible when either it carries no ACL tags (public), or at least
    one of its tags is present in ``allowed_tags``. ``allowed_tags`` is the set
    of tags granted to the requesting principal.
    """

    allowed_tags: frozenset

    @classmethod
    def from_iterable(cls, tags: Iterable[str]) -> "ACLFilter":
        return cls(allowed_tags=frozenset(tags))

    def is_visible(self, chunk: RetrievedChunk) -> bool:
        if not chunk.acl_tags:
            return True
        return any(tag in self.allowed_tags for tag in chunk.acl_tags)

    def apply(
        self, chunks: Sequence[RetrievedChunk]
    ) -> Tuple[List[RetrievedChunk], List[str]]:
        kept: List[RetrievedChunk] = []
        dropped: List[str] = []
        for chunk in chunks:
            if self.is_visible(chunk):
                kept.append(chunk)
            else:
                dropped.append(chunk.chunk_id)
        return kept, dropped
