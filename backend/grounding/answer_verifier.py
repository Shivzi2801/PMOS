"""
answer_verifier.py
==================

Map the grounding signals onto a final VerificationStatus.

WHY THIS FILE EXISTS
--------------------
The pipeline produces several signals (coverage, risk, unsupported claims,
confidence). Somebody has to make the *decision*: ship it, ship-with-warning,
or block it. That decision is policy, and concentrating it in one small,
heavily-documented module means the trust rules are easy to find, review, and
change without touching the measurement code.

RESPONSIBILITIES
----------------
* Apply the documented status rules:
    VERIFIED            - all claims supported (no unsupported claims) AND risk
                          is not HIGH.
    PARTIALLY_VERIFIED  - some claims unsupported but risk is below HIGH.
    REJECTED            - HIGH hallucination risk (major fabrication risk).
* Optionally enforce a minimum confidence floor for VERIFIED.

INPUTS
------
* unsupported_count: int
* hallucination_risk: HallucinationRisk
* confidence_score: float

OUTPUTS
-------
* VerificationDecision(status, rationale)

DESIGN DECISIONS
----------------
* REJECTED is driven by HIGH risk rather than by confidence alone, so the
  reason a user sees ("rejected for fabrication risk") matches the underlying
  cause. Confidence is a *secondary* gate via ``min_confidence_verified``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .grounding_result import HallucinationRisk, VerificationStatus


@dataclass(frozen=True)
class VerificationPolicy:
    """Tunable thresholds for the verification decision."""

    # A VERIFIED answer must have at least this confidence; otherwise it is
    # downgraded to PARTIALLY_VERIFIED.
    min_confidence_verified: float = 0.6


@dataclass
class VerificationDecision:
    status: VerificationStatus
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "rationale": self.rationale}


class AnswerVerifier:
    """Decides the final verification status of an answer."""

    def __init__(self, policy: VerificationPolicy | None = None) -> None:
        self.policy = policy or VerificationPolicy()

    def verify(
        self,
        *,
        unsupported_count: int,
        hallucination_risk: HallucinationRisk,
        confidence_score: float,
    ) -> VerificationDecision:
        # Highest-priority rule: HIGH risk ⇒ reject.
        if hallucination_risk is HallucinationRisk.HIGH:
            return VerificationDecision(
                status=VerificationStatus.REJECTED,
                rationale=(
                    "Rejected due to HIGH hallucination risk; the answer "
                    "contains claims not adequately supported by evidence."
                ),
            )

        if unsupported_count == 0:
            # All claims supported. Apply confidence floor.
            if confidence_score >= self.policy.min_confidence_verified:
                return VerificationDecision(
                    status=VerificationStatus.VERIFIED,
                    rationale=(
                        "All claims are supported by evidence and confidence "
                        "meets the verification threshold."
                    ),
                )
            return VerificationDecision(
                status=VerificationStatus.PARTIALLY_VERIFIED,
                rationale=(
                    "All claims are supported, but overall confidence is below "
                    f"the verification threshold "
                    f"({confidence_score:.2f} < "
                    f"{self.policy.min_confidence_verified:.2f})."
                ),
            )

        # Some unsupported claims, but risk is not HIGH.
        return VerificationDecision(
            status=VerificationStatus.PARTIALLY_VERIFIED,
            rationale=(
                f"{unsupported_count} claim(s) are unsupported; the remainder "
                "are grounded in evidence."
            ),
        )
