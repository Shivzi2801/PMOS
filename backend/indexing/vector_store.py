"""
PMOS S1.5 — Index Fan-Out
vector_store.py

Vector store abstraction.

Defines the `VectorStore` interface that the fan-out orchestrator and the
reconciler depend on. The interface is intentionally minimal and ACL-aware:

  * upsert(points)        — idempotent write keyed by chunk_id
  * delete(tenant_id, ids)
  * fetch_ids(tenant_id, document_id) — for reconciliation
  * search(...)           — ACL-filtered query (retrieval consumers, future)
  * health()              — liveness for the orchestrator/reconciler

Idempotency: upsert is keyed by `IndexPoint.id` (== chunk_id). Re-sending the
same point is a no-op-equivalent overwrite, which is what makes retries and
reindexing safe.

ACL safety: every read path takes a `tenant_id` and search additionally takes
the caller's principals; an implementation MUST filter on the partition key and
the ACL payload field (see qdrant_contract.PAYLOAD_*). The abstraction encodes
this so no concrete store can "forget" tenant scoping.

This module defines the contract only. A concrete in-memory fake is provided
for tests; the Qdrant contract (no real client) lives in qdrant_contract.py.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Dict, FrozenSet, Iterable, List, Mapping, Sequence, Set, Tuple

from .errors import VectorStoreRejected


@dataclasses.dataclass(frozen=True)
class IndexPoint:
    """
    A vector store record. `vector` is supplied by the embedding stage (out of
    scope for S1.5 — the orchestrator accepts an embedder callback). `payload`
    is the ACL-safe partitioned structure built per qdrant_contract.py.
    """

    id: str  # == chunk_id
    tenant_id: str
    vector: Tuple[float, ...]
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.id:
            raise VectorStoreRejected("IndexPoint.id required")
        if not self.tenant_id:
            raise VectorStoreRejected("IndexPoint.tenant_id required")
        if not isinstance(self.vector, tuple) or len(self.vector) == 0:
            raise VectorStoreRejected("IndexPoint.vector must be a non-empty tuple")


@dataclasses.dataclass(frozen=True)
class SearchHit:
    id: str
    score: float
    payload: Mapping[str, object]


class VectorStore(abc.ABC):
    name: str = "vector_store"

    @abc.abstractmethod
    def upsert(self, points: Sequence[IndexPoint]) -> None:
        """Idempotent batch write keyed by point.id."""

    @abc.abstractmethod
    def delete(self, tenant_id: str, ids: Sequence[str]) -> None:
        ...

    @abc.abstractmethod
    def fetch_ids(self, tenant_id: str, document_id: str) -> Set[str]:
        """Return the set of indexed point ids for a document (reconciler)."""

    @abc.abstractmethod
    def search(
        self,
        tenant_id: str,
        query_vector: Sequence[float],
        principals: FrozenSet[str],
        limit: int = 10,
    ) -> List[SearchHit]:
        """ACL-filtered nearest-neighbor search."""

    @abc.abstractmethod
    def health(self) -> bool:
        ...


class InMemoryVectorStore(VectorStore):
    """
    Reference fake for tests. Enforces tenant partitioning and ACL filtering so
    tests exercise the same invariants a real store must uphold. Not for
    production use.
    """

    name = "in_memory_vector_store"

    def __init__(self) -> None:
        # tenant_id -> {id -> IndexPoint}
        self._data: Dict[str, Dict[str, IndexPoint]] = {}
        self._healthy = True

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy

    def upsert(self, points: Sequence[IndexPoint]) -> None:
        for p in points:
            self._data.setdefault(p.tenant_id, {})[p.id] = p

    def delete(self, tenant_id: str, ids: Sequence[str]) -> None:
        bucket = self._data.get(tenant_id, {})
        for i in ids:
            bucket.pop(i, None)

    def fetch_ids(self, tenant_id: str, document_id: str) -> Set[str]:
        bucket = self._data.get(tenant_id, {})
        return {
            pid
            for pid, p in bucket.items()
            if p.payload.get("document_id") == document_id
        }

    def search(
        self,
        tenant_id: str,
        query_vector: Sequence[float],
        principals: FrozenSet[str],
        limit: int = 10,
    ) -> List[SearchHit]:
        bucket = self._data.get(tenant_id, {})
        hits: List[SearchHit] = []
        for p in bucket.values():
            acl = set(p.payload.get("acl", ()))  # type: ignore[arg-type]
            if acl and principals.isdisjoint(acl):
                continue  # ACL: caller cannot see this chunk
            score = _cosine(tuple(query_vector), p.vector)
            hits.append(SearchHit(id=p.id, score=score, payload=p.payload))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def health(self) -> bool:
        return self._healthy

    def all_ids(self, tenant_id: str) -> Set[str]:
        return set(self._data.get(tenant_id, {}).keys())


def _cosine(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    if len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)
