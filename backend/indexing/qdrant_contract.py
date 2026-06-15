"""
PMOS S1.5 — Index Fan-Out
qdrant_contract.py

Qdrant implementation **contract** — NOT a real client.

This module specifies, in code and prose, exactly how PMOS chunks map onto a
Qdrant collection, including the payload partitioning strategy (slice
requirement #6). It contains:

  * The collection naming + partition-key convention.
  * The canonical payload builder (`build_payload`) — the single place that
    decides what leaves the document boundary and how ACL is encoded.
  * A `QdrantContract` describing the operations a real adapter must implement,
    expressed as a no-op stub that raises NotImplementedError. Wiring a real
    qdrant-client is a future infra task and is explicitly out of scope.

Payload partitioning strategy
------------------------------
Single physical collection, logically partitioned by `tenant_id`:

  * Every point payload carries `tenant_id`. A real adapter MUST create a
    payload index on `tenant_id` and MUST add a `tenant_id == <caller>` filter
    to EVERY query. This is the primary isolation boundary.
  * `acl` holds the source ACL principals (list[str]). Queries MUST add a
    `should/must` filter requiring overlap between the caller's principals and
    `acl`. Empty acl ⇒ unreachable (fail-closed) — the adapter must not treat
    empty acl as "public".
  * Point id == chunk_id (deterministic, see chunker.py) ⇒ upserts are
    idempotent and the reconciler can address points directly.

ACL-safe payload structure
---------------------------
The payload deliberately EXCLUDES `content`. Vectors are searched; the original
text is re-fetched from the canonical store at read time under the same ACL.
This prevents the vector store (a wider-blast-radius system) from becoming a
second copy of sensitive text. The payload keeps only:

  document_id, tenant_id, acl, entity_ids, content_hash, ordinal, source_type,
  created_at.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from .document_chunk import DocumentChunk

# Payload field names (centralized so store + reconciler agree).
PAYLOAD_TENANT = "tenant_id"
PAYLOAD_DOCUMENT = "document_id"
PAYLOAD_ACL = "acl"
PAYLOAD_ENTITY_IDS = "entity_ids"
PAYLOAD_CONTENT_HASH = "content_hash"
PAYLOAD_ORDINAL = "ordinal"
PAYLOAD_SOURCE_TYPE = "source_type"
PAYLOAD_CREATED_AT = "created_at"

# Fields a real adapter MUST build a payload index on.
REQUIRED_PAYLOAD_INDEXES = (PAYLOAD_TENANT, PAYLOAD_ACL, PAYLOAD_DOCUMENT)

COLLECTION_NAME = "pmos_document_chunks"


def build_payload(chunk: DocumentChunk) -> Mapping[str, object]:
    """
    Construct the ACL-safe Qdrant payload for a chunk. This is the ONLY
    sanctioned path from a chunk to a stored payload; never inline payload
    construction elsewhere or `content` may leak.
    """
    return {
        PAYLOAD_TENANT: chunk.tenant_id,
        PAYLOAD_DOCUMENT: chunk.document_id,
        # sorted list for deterministic payloads / stable equality in tests
        PAYLOAD_ACL: sorted(chunk.source_acl),
        PAYLOAD_ENTITY_IDS: list(chunk.entity_ids),
        PAYLOAD_CONTENT_HASH: chunk.content_hash,
        PAYLOAD_ORDINAL: chunk.metadata.get("ordinal"),
        PAYLOAD_SOURCE_TYPE: chunk.metadata.get("source_type"),
        PAYLOAD_CREATED_AT: chunk.created_at.isoformat(),
    }


def tenant_filter(tenant_id: str) -> Mapping[str, object]:
    """Filter fragment a real adapter must AND into every query."""
    return {"must": [{"key": PAYLOAD_TENANT, "match": {"value": tenant_id}}]}


def acl_filter(principals: Sequence[str]) -> Mapping[str, object]:
    """
    Filter fragment requiring ACL overlap. With no principals the fragment
    matches nothing (fail-closed).
    """
    if not principals:
        return {"must": [{"key": PAYLOAD_ACL, "match": {"any": []}}]}
    return {"must": [{"key": PAYLOAD_ACL, "match": {"any": list(principals)}}]}


class QdrantContract:
    """
    Specification stub. Documents the operations a real Qdrant adapter must
    provide and the invariants it must uphold. Every method raises
    NotImplementedError — wiring the actual client is out of S1.5 scope.
    """

    collection = COLLECTION_NAME
    required_indexes = REQUIRED_PAYLOAD_INDEXES

    def ensure_collection(self, vector_size: int, distance: str = "Cosine") -> None:
        """
        Create the collection if absent and create payload indexes on
        REQUIRED_PAYLOAD_INDEXES. Idempotent.
        """
        raise NotImplementedError("Qdrant adapter is out of scope for S1.5")

    def upsert(self, points: Sequence[object]) -> None:
        """
        Upsert points keyed by id (== chunk_id). MUST be idempotent. MUST set
        payload via build_payload only.
        """
        raise NotImplementedError("Qdrant adapter is out of scope for S1.5")

    def delete(self, tenant_id: str, ids: Sequence[str]) -> None:
        """Delete by id, scoped by tenant_filter(tenant_id)."""
        raise NotImplementedError("Qdrant adapter is out of scope for S1.5")

    def scroll_ids(self, tenant_id: str, document_id: str) -> set:
        """
        Return all point ids for (tenant_id, document_id) by scrolling with
        tenant_filter AND a document match. Used by the reconciler.
        """
        raise NotImplementedError("Qdrant adapter is out of scope for S1.5")

    def search(
        self,
        tenant_id: str,
        query_vector: Sequence[float],
        principals: Sequence[str],
        limit: int,
    ) -> list:
        """
        kNN search with (tenant_filter AND acl_filter) applied. MUST NOT return
        points the caller's principals cannot access.
        """
        raise NotImplementedError("Qdrant adapter is out of scope for S1.5")

    def health(self) -> bool:
        raise NotImplementedError("Qdrant adapter is out of scope for S1.5")
