"""CanonicalDocument input contract.

This is the output of Wave 1 Slice 1.2 (Connector → Normalization → PII
Screening → Injection Screening). The extraction engine treats it as a
read-only input. We define a structural protocol plus a lightweight concrete
shape so the pipeline can be tested in isolation without importing the
ingestion module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@runtime_checkable
class CanonicalDocument(Protocol):
    """Structural contract for the canonical document consumed by extraction.

    Any object exposing these attributes is accepted by the pipeline. This
    avoids a hard dependency on the ingestion module's concrete class.
    """

    documentId: str
    tenantId: str
    text: str
    metadata: Mapping[str, Any]


@dataclass
class CanonicalDocumentModel:
    """Concrete, validation-bearing implementation of CanonicalDocument.

    Used by tests and by callers that do not already hold an ingestion-side
    instance. ``text`` is the normalized, PII/injection-screened body.
    """

    documentId: str
    tenantId: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.documentId:
            raise ValueError("CanonicalDocument.documentId is required")
        if not self.tenantId:
            raise ValueError("CanonicalDocument.tenantId is required")
        if self.text is None:
            raise ValueError("CanonicalDocument.text must not be None")


def validate_canonical_document(doc: Optional[Any]) -> None:
    """Raise ``MalformedDocumentError`` if ``doc`` is unusable.

    A document is malformed if it is ``None``, missing required attributes, or
    its ``text``/identifier fields are of the wrong type.
    """
    # Imported here to avoid a circular import at module load time.
    from .errors import MalformedDocumentError

    if doc is None:
        raise MalformedDocumentError("CanonicalDocument is None")

    for attr in ("documentId", "tenantId", "text", "metadata"):
        if not hasattr(doc, attr):
            raise MalformedDocumentError(f"CanonicalDocument missing '{attr}'")

    if not isinstance(doc.documentId, str) or not doc.documentId:
        raise MalformedDocumentError("documentId must be a non-empty string")
    if not isinstance(doc.tenantId, str) or not doc.tenantId:
        raise MalformedDocumentError("tenantId must be a non-empty string")
    if not isinstance(doc.text, str):
        raise MalformedDocumentError("text must be a string")
    if not isinstance(doc.metadata, Mapping):
        raise MalformedDocumentError("metadata must be a mapping")
