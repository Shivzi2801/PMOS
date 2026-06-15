"""Provenance models for PMOS Wave 1 Slice 1.4.

Every resolved canonical entity must retain its source lineage. A
ProvenanceRecord captures *where* an atom that contributed to an entity
came from: the connector, the document, the tenant, and the literal
evidence text that supported the extraction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceType(str, enum.Enum):
    """Coarse classification of where a record originated."""

    DOCUMENT = "DOCUMENT"
    MESSAGE = "MESSAGE"
    RECORD = "RECORD"
    API = "API"
    UNKNOWN = "UNKNOWN"


class ConnectorType(str, enum.Enum):
    """The connector family that produced the source."""

    GDRIVE = "GDRIVE"
    SLACK = "SLACK"
    SALESFORCE = "SALESFORCE"
    GMAIL = "GMAIL"
    NOTION = "NOTION"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class ProvenanceRecord:
    """Source lineage for a single extraction that fed entity resolution.

    Attributes:
        source_id: Identifier of the originating source item.
        source_type: Coarse source classification.
        document_id: Document the extraction came from (may equal source_id).
        connector_type: Connector family that produced the source.
        connection_id: The specific tenant connection / installation.
        tenant_id: Owning tenant; used for ACL scoping.
        extraction_id: Identity of the extraction atom.
        evidence_text: The literal text supporting the extraction.
        created_at: When this provenance record was captured.
    """

    source_id: str
    source_type: SourceType
    document_id: str
    connector_type: ConnectorType
    connection_id: str
    tenant_id: str
    extraction_id: str
    evidence_text: str
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        # A provenance record with no source identity is an orphan and is
        # never valid; the merge pipeline rejects these before they attach.
        if not self.source_id:
            raise ValueError("ProvenanceRecord requires a non-empty source_id")
        if not self.tenant_id:
            raise ValueError("ProvenanceRecord requires a non-empty tenant_id")

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "document_id": self.document_id,
            "connector_type": self.connector_type.value,
            "connection_id": self.connection_id,
            "tenant_id": self.tenant_id,
            "extraction_id": self.extraction_id,
            "evidence_text": self.evidence_text,
            "created_at": self.created_at.isoformat(),
        }
