"""
PMOS Observability & Monitoring — Grounding Metrics (S2.6)

Typed facade for the Grounding/verification slice (S1.9). Captures grounding
latency, the confidence score of a verified answer, and the count of
unsupported claims — the core quality signals for hallucination monitoring.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics_collector import MetricsCollector


@dataclass(frozen=True)
class GroundingOutcome:
    tenant_id: str
    duration_ms: float
    score: float
    unsupported_claims: int = 0


class GroundingMetrics:
    M_LATENCY = "pmos.grounding.latency_ms"
    M_SCORE = "pmos.grounding.score"
    M_UNSUPPORTED = "pmos.grounding.unsupported_claims"

    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    def record(self, outcome: GroundingOutcome) -> None:
        labels = {"tenant": outcome.tenant_id}
        self._collector.observe(self.M_LATENCY, outcome.duration_ms, labels=labels)
        self._collector.observe(self.M_SCORE, outcome.score, labels=labels)
        self._collector.observe(
            self.M_UNSUPPORTED, float(outcome.unsupported_claims), labels=labels
        )


__all__ = ["GroundingMetrics", "GroundingOutcome"]
