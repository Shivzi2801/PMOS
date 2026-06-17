"""
hallucination_detector.py
=========================

Detect hallucination risk in a generated answer.

WHY THIS FILE EXISTS
--------------------
"Hallucination" is the failure mode enterprise customers fear most: a fluent,
confident-sounding answer that is not backed by their data. This module
converts the per-claim support picture into a single, explainable risk band
(LOW / MEDIUM / HIGH) plus the list of offending claims.

It is deliberately separate from confidence scoring. Risk is a *policy* signal
("should a human look at this?"), whereas confidence is a *continuous* quality
estimate. Keeping them apart lets enterprises tune their review gate
(risk thresholds) independently of how they display confidence.

RESPONSIBILITIES
----------------
* Identify unsupported claims (NO_SUPPORT).
* Apply threshold rules to assign a HallucinationRisk band.
* Return the unsupported-claim ids for the audit trail and UI highlighting.

INPUTS
------
* claims: list[Claim]
* best_by_claim: dict[claim_id, EvidenceMatch]
* citation_coverage: float

OUTPUTS
-------
* HallucinationReport(risk, unsupported_claim_ids, reasons)

DESIGN DECISIONS
----------------
* Two independent triggers escalate to HIGH:
    1. absolute count of unsupported claims exceeds ``max_unsupported``, OR
    2. citation_coverage falls below ``min_coverage_for_medium``.
  Either condition alone is enough — fabrication is asymmetric-cost, so we
  bias toward caution.
* Thresholds are injected, never hard-coded at the call site, so different
  enterprise tenants can run stricter or looser gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .grounding_result import Claim, EvidenceMatch, HallucinationRisk, SupportLevel


@dataclass(frozen=True)
class HallucinationThresholds:
    """Tunable thresholds for risk banding."""

    # More than this many unsupported claims ⇒ HIGH.
    max_unsupported: int = 1
    # Coverage below this ⇒ at least MEDIUM; well below ⇒ HIGH.
    min_coverage_for_low: float = 0.9
    min_coverage_for_medium: float = 0.6


@dataclass
class HallucinationReport:
    risk: HallucinationRisk
    unsupported_claim_ids: list[str]
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk.value,
            "unsupported_claim_ids": list(self.unsupported_claim_ids),
            "reasons": list(self.reasons),
        }


class HallucinationDetector:
    """Computes a hallucination risk band."""

    def __init__(self, thresholds: HallucinationThresholds | None = None) -> None:
        self.thresholds = thresholds or HallucinationThresholds()

    def detect(
        self,
        claims: list[Claim],
        best_by_claim: dict[str, EvidenceMatch],
        citation_coverage: float,
    ) -> HallucinationReport:
        unsupported = [
            claim.claim_id
            for claim in claims
            if self._support_level(claim, best_by_claim) is SupportLevel.NO_SUPPORT
        ]
        reasons: list[str] = []
        t = self.thresholds

        # No claims ⇒ nothing to hallucinate.
        if not claims:
            return HallucinationReport(
                risk=HallucinationRisk.LOW,
                unsupported_claim_ids=[],
                reasons=["No factual claims present."],
            )

        unsupported_count = len(unsupported)
        risk = HallucinationRisk.LOW

        # Coverage-driven escalation.
        if citation_coverage < t.min_coverage_for_medium:
            risk = HallucinationRisk.HIGH
            reasons.append(
                f"Citation coverage {citation_coverage:.2f} is below the medium "
                f"threshold {t.min_coverage_for_medium:.2f}."
            )
        elif citation_coverage < t.min_coverage_for_low:
            risk = self._escalate(risk, HallucinationRisk.MEDIUM)
            reasons.append(
                f"Citation coverage {citation_coverage:.2f} is below the low-risk "
                f"threshold {t.min_coverage_for_low:.2f}."
            )

        # Count-driven escalation.
        if unsupported_count > t.max_unsupported:
            risk = HallucinationRisk.HIGH
            reasons.append(
                f"{unsupported_count} unsupported claims exceed the maximum "
                f"allowed ({t.max_unsupported})."
            )
        elif unsupported_count >= 1:
            risk = self._escalate(risk, HallucinationRisk.MEDIUM)
            reasons.append(
                f"{unsupported_count} unsupported claim(s) detected."
            )

        if risk is HallucinationRisk.LOW:
            reasons.append("All claims are supported and coverage is high.")

        return HallucinationReport(
            risk=risk,
            unsupported_claim_ids=unsupported,
            reasons=reasons,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _support_level(
        claim: Claim, best_by_claim: dict[str, EvidenceMatch]
    ) -> SupportLevel:
        best = best_by_claim.get(claim.claim_id)
        return best.support_level if best else SupportLevel.NO_SUPPORT

    @staticmethod
    def _escalate(
        current: HallucinationRisk, candidate: HallucinationRisk
    ) -> HallucinationRisk:
        order = {
            HallucinationRisk.LOW: 0,
            HallucinationRisk.MEDIUM: 1,
            HallucinationRisk.HIGH: 2,
        }
        return current if order[current] >= order[candidate] else candidate
