"""Data contracts shared across the ingestion pipeline.

These are the stable, serializable shapes that cross module boundaries.
Every downstream consumer (Normalization, PII, Injection, Quarantine) depends
only on the definitions in this package — never on another module's internals.
"""

from .enums import PIISeverity, PIIType, InjectionStatus, InjectionCategory, RedactionMode
from .canonical_document import CanonicalDocument, Provenance, SourceMetadata
from .pii_finding import PIIFinding
from .injection_finding import InjectionFinding, InjectionMatch
from .quarantine_record import QuarantineRecord

__all__ = [
    "PIISeverity",
    "PIIType",
    "InjectionStatus",
    "InjectionCategory",
    "RedactionMode",
    "CanonicalDocument",
    "Provenance",
    "SourceMetadata",
    "PIIFinding",
    "InjectionFinding",
    "InjectionMatch",
    "QuarantineRecord",
]
