"""End-to-end ingestion pipeline.

    Raw Connector Record
        -> Normalization
        -> PII Detection
        -> Prompt Injection Screening
        -> Safe Canonical Document  (PUBLISHED)
                or
        -> Quarantine               (QUARANTINED)

Policy:
  * Injection status QUARANTINED      -> quarantine immediately.
  * Injection status SUSPECT          -> quarantine (conservative default).
  * CRITICAL PII (API keys / tokens)  -> quarantine.
  * Other PII                         -> redact in body, publish, annotate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..contracts import (
    CanonicalDocument,
    InjectionFinding,
    InjectionStatus,
    PIISeverity,
    RedactionMode,
)
from ..normalization import registry as default_registry, NormalizationError
from ..normalization.registry import NormalizerRegistry
from ..pii import PIIEngine, PIIResult
from ..injection import InjectionScreener
from ..quarantine import QuarantineService


class PipelineOutcome(str, Enum):
    PUBLISHED = "PUBLISHED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


@dataclass
class PipelineResult:
    outcome: PipelineOutcome
    document: Optional[CanonicalDocument] = None
    pii_result: Optional[PIIResult] = None
    injection_finding: Optional[InjectionFinding] = None
    quarantine_id: Optional[str] = None
    reason: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "quarantine_id": self.quarantine_id,
            "errors": list(self.errors),
            "document": self.document.to_dict() if self.document else None,
            "pii": self.pii_result.to_summary() if self.pii_result else None,
            "injection": (
                self.injection_finding.to_dict() if self.injection_finding else None
            ),
        }


class IngestionPipeline:
    def __init__(
        self,
        *,
        normalizer_registry: Optional[NormalizerRegistry] = None,
        pii_engine: Optional[PIIEngine] = None,
        screener: Optional[InjectionScreener] = None,
        quarantine_service: Optional[QuarantineService] = None,
        pii_mode: RedactionMode = RedactionMode.REDACT,
        quarantine_on_suspect: bool = True,
    ) -> None:
        self._registry = normalizer_registry or default_registry
        self._pii = pii_engine or PIIEngine()
        self._screener = screener or InjectionScreener()
        self._quarantine = quarantine_service or QuarantineService()
        self._pii_mode = pii_mode
        self._quarantine_on_suspect = quarantine_on_suspect

    def process(
        self, raw: Dict[str, Any], *, connector_type: str, connector_id: str
    ) -> PipelineResult:
        # --- Stage 1: Normalization ------------------------------------------
        try:
            doc = self._registry.normalize(
                connector_type, raw, connector_id=connector_id
            )
        except NormalizationError as exc:
            return PipelineResult(
                outcome=PipelineOutcome.FAILED,
                reason=f"normalization_failed: {exc}",
                errors=[str(exc)],
            )

        # --- Stage 2: PII Detection ------------------------------------------
        scan_target = f"{doc.title}\n\n{doc.body}"
        pii_result = self._pii.scan(scan_target, mode=self._pii_mode)

        # --- Stage 3: Prompt Injection Screening -----------------------------
        injection = self._screener.screen(scan_target)

        # --- Stage 4: Decision -----------------------------------------------
        critical_pii = [
            f for f in pii_result.findings if f.severity == PIISeverity.CRITICAL
        ]

        must_quarantine = (
            injection.status == InjectionStatus.QUARANTINED
            or (
                injection.status == InjectionStatus.SUSPECT
                and self._quarantine_on_suspect
            )
            or bool(critical_pii)
        )

        if must_quarantine:
            reason = self._build_reason(injection, critical_pii)
            record = self._quarantine.quarantine(
                provenance=doc.provenance,
                reason=reason,
                original_payload=raw,
                injection_finding=injection,
                pii_findings=pii_result.findings,
                canonical=doc,
            )
            return PipelineResult(
                outcome=PipelineOutcome.QUARANTINED,
                document=doc,
                pii_result=pii_result,
                injection_finding=injection,
                quarantine_id=record.quarantine_id,
                reason=reason,
            )

        # --- Publish (with redaction + annotations) --------------------------
        published = doc
        if self._pii_mode == RedactionMode.REDACT and pii_result.has_findings:
            # Re-redact body only (title preserved separately if needed).
            body_scan = self._pii.scan(doc.body, mode=RedactionMode.REDACT)
            published = doc.with_body(body_scan.redacted_text or doc.body)

        published.annotations["pii"] = pii_result.to_summary()
        published.annotations["injection"] = injection.to_dict()

        return PipelineResult(
            outcome=PipelineOutcome.PUBLISHED,
            document=published,
            pii_result=pii_result,
            injection_finding=injection,
            reason="published",
        )

    @staticmethod
    def _build_reason(
        injection: InjectionFinding, critical_pii: list
    ) -> str:
        parts: List[str] = []
        if injection.status in (InjectionStatus.SUSPECT, InjectionStatus.QUARANTINED):
            cats = ", ".join(c.value for c in injection.categories)
            parts.append(
                f"prompt_injection={injection.status.value} "
                f"(score={injection.score}; categories=[{cats}])"
            )
        if critical_pii:
            types = ", ".join(sorted({f.pii_type.value for f in critical_pii}))
            parts.append(f"critical_pii=[{types}]")
        return " | ".join(parts) or "policy_violation"
