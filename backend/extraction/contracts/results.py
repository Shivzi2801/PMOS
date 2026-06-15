"""Result and context contracts for the extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Tuple

from .atoms import Atom

# Bump when the extraction logic changes in a way that affects output semantics.
EXTRACTION_VERSION = "1.3.0"


@dataclass
class ExtractionContext:
    """Per-invocation context threaded through every extractor.

    Carries tenant scoping, a correlation id for distributed tracing, and
    arbitrary document metadata (source type, language, ingestion timestamp,
    etc.) surfaced from the CanonicalDocument.
    """

    tenantId: str
    correlationId: str
    documentMetadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenantId:
            raise ValueError("ExtractionContext.tenantId is required")
        if not self.correlationId:
            raise ValueError("ExtractionContext.correlationId is required")


@dataclass
class ExtractionResult:
    """Final output of the extraction pipeline for a single document."""

    documentId: str
    atoms: List[Atom] = field(default_factory=list)
    extractionVersion: str = EXTRACTION_VERSION
    latencyMs: float = 0.0

    @property
    def atom_count(self) -> int:
        return len(self.atoms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "documentId": self.documentId,
            "atoms": [a.to_dict() for a in self.atoms],
            "extractionVersion": self.extractionVersion,
            "latencyMs": self.latencyMs,
            "atomCount": self.atom_count,
        }
