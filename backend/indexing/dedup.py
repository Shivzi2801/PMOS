"""
PMOS S1.5 — Index Fan-Out
dedup.py

Content-hash deduplication.

Goal: avoid indexing the same content twice. The dedup key is the
content_hash (hashing.py). Two chunks with identical normalized content are
duplicates and only one needs a vector.

Scoping: dedup state is **tenant-scoped**. A hash seen for tenant A says
nothing about tenant B — collapsing across tenants would leak the existence of
content across an ACL boundary and is forbidden. The store key is therefore
(tenant_id, content_hash).

Collision handling: SHA256 collisions are infeasible, so if we see the same
(tenant_id, content_hash) we treat it as a true duplicate. The store records
the *first* chunk's content fingerprint length so that a mismatch (same hash,
different length) can be detected and raised as HashCollisionError — this is a
defensive guard against a hashing bug rather than a real cryptographic event.

This module defines an interface plus an in-memory implementation. A
production store (e.g. backed by the chunk table from a future slice) would
implement the same interface; S1.5 keeps it pure-Python.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Dict, Iterable, List, Tuple

from .document_chunk import DocumentChunk
from .errors import HashCollisionError


@dataclasses.dataclass(frozen=True)
class DedupResult:
    unique: Tuple[DocumentChunk, ...]
    duplicates: Tuple[DocumentChunk, ...]

    @property
    def deduplicated_count(self) -> int:
        return len(self.duplicates)


class DedupStore(abc.ABC):
    """Records which (tenant_id, content_hash) pairs have been seen."""

    @abc.abstractmethod
    def seen(self, tenant_id: str, content_hash: str) -> bool:
        ...

    @abc.abstractmethod
    def record(self, chunk: DocumentChunk) -> None:
        ...

    @abc.abstractmethod
    def fingerprint(self, tenant_id: str, content_hash: str) -> int | None:
        """Return the recorded content length for collision checking."""


class InMemoryDedupStore(DedupStore):
    def __init__(self) -> None:
        # (tenant_id, hash) -> content length
        self._seen: Dict[Tuple[str, str], int] = {}

    def seen(self, tenant_id: str, content_hash: str) -> bool:
        return (tenant_id, content_hash) in self._seen

    def record(self, chunk: DocumentChunk) -> None:
        self._seen[(chunk.tenant_id, chunk.content_hash)] = len(chunk.content)

    def fingerprint(self, tenant_id: str, content_hash: str) -> int | None:
        return self._seen.get((tenant_id, content_hash))


class Deduplicator:
    def __init__(self, store: DedupStore) -> None:
        self.store = store

    def deduplicate(self, chunks: Iterable[DocumentChunk]) -> DedupResult:
        """
        Partition chunks into unique vs duplicate. Dedup is applied both
        against prior runs (store) AND within this batch (a document repeating
        a paragraph). Order is preserved for the unique set.
        """
        unique: List[DocumentChunk] = []
        duplicates: List[DocumentChunk] = []
        batch_seen: Dict[Tuple[str, str], int] = {}

        for chunk in chunks:
            key = (chunk.tenant_id, chunk.content_hash)

            recorded_len = self.store.fingerprint(*key)
            batch_len = batch_seen.get(key)

            prior_len = recorded_len if recorded_len is not None else batch_len

            if prior_len is not None:
                if prior_len != len(chunk.content):
                    raise HashCollisionError(
                        "content_hash reused for differing content length",
                        tenant_id=chunk.tenant_id,
                        document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                    )
                duplicates.append(chunk)
                continue

            unique.append(chunk)
            batch_seen[key] = len(chunk.content)
            self.store.record(chunk)

        return DedupResult(unique=tuple(unique), duplicates=tuple(duplicates))
