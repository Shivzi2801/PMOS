"""
confidence_scorer.py
====================

Compute a single 0.0 → 1.0 confidence score for a verified answer.

WHY THIS FILE EXISTS
--------------------
Users and downstream systems want one number that says "how much should I trust
this answer?". This module fuses the four independent signals the slice
produces into a calibrated, explainable scalar:

  1. retrieval relevance   - how good was the underlying evidence?
  2. citation coverage     - what fraction of claims are grounded?
  3. evidence support      - how strong is the support, on average?
  4. hallucination risk    - a penalty for detected fabrication risk.

RESPONSIBILITIES
----------------
* Combine the four signals with configurable weights.
* Apply a multiplicative penalty for hallucination risk so that a single HIGH
  risk answer can never present as high-confidence.
* Return both the score and a per-signal breakdown for transparency / audit.

INPUTS
------
* retrieval_relevance: float (0..1)  — aggregate of evidence relevance scores
* citation_coverage: float (0..1)
* avg_support_weight: float (0..1)   — mean SupportLevel.weight over claims
* hallucination_risk: HallucinationRisk

OUTPUTS
-------
* ConfidenceBreakdown(score, components, penalty)

DESIGN DECISIONS
----------------
* Weights sum to 1.0 for the additive base; the risk penalty is *multiplicative*
  on top, because risk should be able to veto a high base score rather than just
  shift it linearly.
* The function is pure and deterministic — same inputs always yield the same
  score — which auditors and tests both rely on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import ConfidenceComputationError
from .grounding_result import HallucinationRisk


@dataclass(frozen=True)
class ConfidenceWeights:
    """Additive weights for the base confidence signals. Must sum to 1.0."""

    retrieval_relevance: float = 0.25
    citation_coverage: float = 0.35
    evidence_support: float = 0.40

    def validate(self) -> None:
        total = (
            self.retrieval_relevance
            + self.citation_coverage
            + self.evidence_support
        )
        if abs(total - 1.0) > 1e-6:
            raise ConfidenceComputationError(
                "Confidence weights must sum to 1.0.",
                details={"sum": total},
            )


# Multiplicative penalty applied based on hallucination risk band.
_RISK_PENALTY = {
    HallucinationRisk.LOW: 1.0,
    HallucinationRisk.MEDIUM: 0.7,
    HallucinationRisk.HIGH: 0.3,
}


@dataclass
class ConfidenceBreakdown:
    score: float
    components: dict[str, float] = field(default_factory=dict)
    risk_penalty: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "components": dict(self.components),
            "risk_penalty": self.risk_penalty,
        }


class ConfidenceScorer:
    """Fuses grounding signals into a single confidence score."""

    def __init__(self, weights: ConfidenceWeights | None = None) -> None:
        self.weights = weights or ConfidenceWeights()
        self.weights.validate()

    def score(
        self,
        *,
        retrieval_relevance: float,
        citation_coverage: float,
        avg_support_weight: float,
        hallucination_risk: HallucinationRisk,
    ) -> ConfidenceBreakdown:
        try:
            r = self._clamp(retrieval_relevance)
            c = self._clamp(citation_coverage)
            s = self._clamp(avg_support_weight)

            w = self.weights
            base = (
                w.retrieval_relevance * r
                + w.citation_coverage * c
                + w.evidence_support * s
            )
            penalty = _RISK_PENALTY[hallucination_risk]
            final = round(self._clamp(base * penalty), 4)

            return ConfidenceBreakdown(
                score=final,
                components={
                    "retrieval_relevance": round(r, 4),
                    "citation_coverage": round(c, 4),
                    "evidence_support": round(s, 4),
                    "base_before_penalty": round(base, 4),
                },
                risk_penalty=penalty,
            )
        except ConfidenceComputationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ConfidenceComputationError(
                "Failed to compute confidence score.",
                details={"cause": str(exc)},
            ) from exc

    @staticmethod
    def _clamp(value: float) -> float:
        if value is None:
            return 0.0
        return max(0.0, min(1.0, float(value)))
