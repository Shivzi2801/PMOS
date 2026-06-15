"""PII engine — runs all detectors and applies redact / annotate behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..contracts import PIIFinding, PIIType, PIISeverity, RedactionMode
from .detectors import Detector, DEFAULT_DETECTORS, _dedupe_spans


@dataclass
class PIIResult:
    """Outcome of scanning one piece of text for PII."""

    findings: List[PIIFinding] = field(default_factory=list)
    redacted_text: Optional[str] = None  # populated only in REDACT mode
    mode: RedactionMode = RedactionMode.ANNOTATE

    @property
    def has_findings(self) -> bool:
        return len(self.findings) > 0

    @property
    def max_severity(self) -> Optional[PIISeverity]:
        if not self.findings:
            return None
        return max((f.severity for f in self.findings), key=lambda s: s.rank)

    def counts_by_type(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.findings:
            out[f.pii_type.value] = out.get(f.pii_type.value, 0) + 1
        return out

    def to_summary(self) -> Dict[str, object]:
        return {
            "count": len(self.findings),
            "max_severity": self.max_severity.value if self.max_severity else None,
            "counts_by_type": self.counts_by_type(),
            "findings": [f.to_dict() for f in self.findings],
        }


def _placeholder(pii_type: PIIType) -> str:
    return f"[REDACTED:{pii_type.value}]"


class PIIEngine:
    """Coordinates the configured detectors over input text."""

    def __init__(
        self,
        detectors: Optional[List[Detector]] = None,
        *,
        min_confidence: float = 0.5,
    ) -> None:
        self._detectors = detectors if detectors is not None else list(DEFAULT_DETECTORS)
        self._min_confidence = min_confidence

    def scan(
        self, text: str, *, mode: RedactionMode = RedactionMode.ANNOTATE
    ) -> PIIResult:
        if not text:
            return PIIResult(findings=[], redacted_text=None, mode=mode)

        raw_findings: List[PIIFinding] = []
        for det in self._detectors:
            raw_findings.extend(det.detect(text))

        # Filter low-confidence, then resolve overlaps across detectors.
        filtered = [f for f in raw_findings if f.confidence >= self._min_confidence]
        findings = _dedupe_spans(filtered)

        result = PIIResult(findings=findings, mode=mode)
        if mode == RedactionMode.REDACT:
            result.redacted_text = self._redact(text, findings)
        return result

    @staticmethod
    def _redact(text: str, findings: List[PIIFinding]) -> str:
        # Replace from the end so earlier offsets stay valid.
        out = text
        for f in sorted(findings, key=lambda x: x.start, reverse=True):
            out = out[: f.start] + _placeholder(f.pii_type) + out[f.end :]
        return out
