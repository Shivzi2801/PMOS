"""The CanonicalDocument contract — the safe, normalized PMOS document.

A CanonicalDocument is the single output shape every connector is normalized
into. It carries the cleaned content plus full provenance so any downstream
PMOS component can trace a document back to its exact origin.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SourceMetadata:
    """Connector-specific metadata preserved verbatim from the raw record.

    This is intentionally a free-form bag so each connector can keep fields
    that are meaningful to its source system (priority, tags, status, etc.)
    without forcing a schema change in the canonical contract.
    """

    fields: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.fields)


@dataclass
class Provenance:
    """Where a document came from and how it travelled through the pipeline.

    Provenance fields must NEVER be dropped — they are required for audit,
    quarantine traceback, and re-ingestion.
    """

    connector_id: str            # logical connector instance, e.g. "zendesk-prod"
    connector_type: str          # connector family, e.g. "zendesk"
    source_id: str               # native record id in the source system
    source_type: str             # native record type, e.g. "ticket", "comment"
    source_url: Optional[str] = None
    source_created_at: Optional[str] = None   # original creation time (ISO 8601)
    source_updated_at: Optional[str] = None   # original last-update time (ISO 8601)
    ingested_at: str = field(default_factory=_utc_now_iso)
    sync_cursor: Optional[str] = None         # sync-state cursor from Slice 1.1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalDocument:
    """The normalized, safe representation of one source record."""

    title: str
    body: str
    provenance: Provenance
    source_metadata: SourceMetadata = field(default_factory=SourceMetadata)
    document_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    language: Optional[str] = None
    content_hash: Optional[str] = None
    # Annotations attached by later pipeline stages (PII / injection summaries).
    annotations: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.content_hash is None:
            self.content_hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Deterministic hash of the canonical content for dedup / integrity."""
        h = hashlib.sha256()
        h.update(self.title.encode("utf-8"))
        h.update(b"\x00")
        h.update(self.body.encode("utf-8"))
        return h.hexdigest()

    def with_body(self, new_body: str) -> "CanonicalDocument":
        """Return a copy with replaced body (used by PII redaction)."""
        return CanonicalDocument(
            title=self.title,
            body=new_body,
            provenance=self.provenance,
            source_metadata=self.source_metadata,
            document_id=self.document_id,
            language=self.language,
            content_hash=None,  # recompute
            annotations=dict(self.annotations),
            schema_version=self.schema_version,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "schema_version": self.schema_version,
            "title": self.title,
            "body": self.body,
            "language": self.language,
            "content_hash": self.content_hash,
            "provenance": self.provenance.to_dict(),
            "source_metadata": self.source_metadata.to_dict(),
            "annotations": dict(self.annotations),
        }
