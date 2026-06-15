"""
PMOS S1.5 — Index Fan-Out
document_chunk.py

The `document_chunks` contract.

A DocumentChunk is the atomic unit that gets indexed. It is derived from a
canonical document (S1.2 normalization) and carries forward the resolved
entity references (S1.4) and the source ACL (S1.4 Source ACL enforcement) so
that retrieval can enforce access control *at query time* without re-joining to
the source document.

Invariants (validated in `__post_init__`):
  * All required fields present and non-empty where a value is mandatory.
  * tenant_id is present — every chunk is tenant-scoped; there is no global
    chunk. This is the root of the payload partitioning strategy (see
    qdrant_contract.py).
  * content_hash, when supplied, is a lowercase hex SHA256 (64 chars). The
    hashing module is the canonical producer; the contract only checks shape.
  * source_acl is a frozenset of principal identifiers (copied from the source
    document). An empty ACL means "no principals" → not retrievable, which is
    fail-closed and intentional.
  * entity_ids references resolved canonical entities (S1.4). May be empty for
    chunks that contain no extracted entities.

Content safety:
  * `content` holds normalized, post-PII-screening text (S1.2). The contract
    does not re-screen; it trusts the upstream quarantine gate. It DOES refuse
    to serialize content into ACL-wide telemetry — see `safe_descriptor()`.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import re
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

from .errors import ContractViolation

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Fields that must be present on every chunk per the slice contract.
REQUIRED_FIELDS: Tuple[str, ...] = (
    "chunk_id",
    "tenant_id",
    "document_id",
    "entity_ids",
    "source_acl",
    "content",
    "content_hash",
    "metadata",
    "created_at",
)


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


@dataclasses.dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    tenant_id: str
    document_id: str
    entity_ids: Tuple[str, ...]
    source_acl: FrozenSet[str]
    content: str
    content_hash: str
    metadata: Mapping[str, Any]
    created_at: _dt.datetime

    def __post_init__(self) -> None:
        self._validate()

    # --- validation --------------------------------------------------------

    def _validate(self) -> None:
        def violation(msg: str) -> ContractViolation:
            return ContractViolation(
                msg,
                tenant_id=getattr(self, "tenant_id", None),
                document_id=getattr(self, "document_id", None),
                chunk_id=getattr(self, "chunk_id", None),
            )

        for field in ("chunk_id", "tenant_id", "document_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise violation(f"{field} must be a non-empty string")

        if not isinstance(self.content, str) or self.content == "":
            raise violation("content must be a non-empty string")

        if not isinstance(self.content_hash, str) or not _SHA256_RE.match(
            self.content_hash
        ):
            raise violation("content_hash must be a lowercase hex SHA256 digest")

        if not isinstance(self.entity_ids, tuple):
            raise violation("entity_ids must be a tuple")
        if any((not isinstance(e, str) or not e) for e in self.entity_ids):
            raise violation("entity_ids must contain non-empty strings")

        if not isinstance(self.source_acl, frozenset):
            raise violation("source_acl must be a frozenset")
        if any((not isinstance(p, str) or not p) for p in self.source_acl):
            raise violation("source_acl principals must be non-empty strings")

        if not isinstance(self.metadata, Mapping):
            raise violation("metadata must be a mapping")

        if not isinstance(self.created_at, _dt.datetime):
            raise violation("created_at must be a datetime")
        if self.created_at.tzinfo is None:
            raise violation("created_at must be timezone-aware (UTC)")

    # --- helpers -----------------------------------------------------------

    @property
    def is_retrievable(self) -> bool:
        """Fail-closed: a chunk with an empty ACL is not retrievable."""
        return len(self.source_acl) > 0

    def safe_descriptor(self) -> Dict[str, Any]:
        """
        ACL-safe projection for logs/metrics/reports. Deliberately omits
        `content` and `entity_ids` (entity membership can itself be sensitive).
        """
        return {
            "chunk_id": self.chunk_id,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "content_hash": self.content_hash,
            "acl_size": len(self.source_acl),
            "created_at": self.created_at.isoformat(),
        }

    def with_metadata(self, **extra: Any) -> "DocumentChunk":
        """Return a copy with metadata merged (enrichment is additive)."""
        merged = dict(self.metadata)
        merged.update(extra)
        return dataclasses.replace(self, metadata=merged)


def build_chunk(
    *,
    chunk_id: str,
    tenant_id: str,
    document_id: str,
    content: str,
    content_hash: str,
    entity_ids: Tuple[str, ...] = (),
    source_acl: FrozenSet[str] = frozenset(),
    metadata: Optional[Mapping[str, Any]] = None,
    created_at: Optional[_dt.datetime] = None,
) -> DocumentChunk:
    """Convenience constructor that fills timestamp/metadata defaults."""
    return DocumentChunk(
        chunk_id=chunk_id,
        tenant_id=tenant_id,
        document_id=document_id,
        entity_ids=tuple(entity_ids),
        source_acl=frozenset(source_acl),
        content=content,
        content_hash=content_hash,
        metadata=dict(metadata or {}),
        created_at=created_at or _utcnow(),
    )
