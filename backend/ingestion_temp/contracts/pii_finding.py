"""The PIIFinding contract — one detected piece of sensitive information."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict

from .enums import PIIType, PIISeverity


@dataclass
class PIIFinding:
    """A single PII / secret detection within a document body.

    The raw matched value is stored only as a short preview (never the full
    secret) so findings can be logged safely.
    """

    pii_type: PIIType
    severity: PIISeverity
    confidence: float            # 0.0 - 1.0
    start: int                   # char offset (inclusive)
    end: int                     # char offset (exclusive)
    preview: str                 # masked/truncated sample of the match
    detector: str                # name of the detector that produced it

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span [{self.start},{self.end})")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pii_type"] = self.pii_type.value
        d["severity"] = self.severity.value
        return d
