"""
PMOS Observability & Monitoring — Retrieval Metrics (S2.6)

A typed facade over :class:`MetricsCollector` for the Retrieval slice (S1.6).
Instead of scattering raw metric-name strings and label dicts across the
retrieval code, callers depend on this narrow, well-named API. The facade owns
the mapping from domain concepts to registry metric names, so a metric rename
is a one-line change here rather than a platform-wide grep.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .metrics_collector import MetricsCollector


@dataclass(frozen=True)
class RetrievalOutcome:
    """Structured result of a retrieval operation, ready to be recorded."""

    strategy: str
    tenant_id: str
    duration_ms: float
    result_count: int
    top_score: Optional[float] = None


class RetrievalMetrics:
    """Records retrieval timing, result-set size, score quality, and misses."""

    M_LATENCY = "pmos.retrieval.latency_ms"
    M_RESULTS = "pmos.retrieval.results"
    M_TOP_SCORE = "pmos.retrieval.top_score"
    M_EMPTY = "pmos.retrieval.empty_total"

    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    def record(self, outcome: RetrievalOutcome) -> None:
        labels = {"strategy": outcome.strategy, "tenant": outcome.tenant_id}
        self._collector.observe(self.M_LATENCY, outcome.duration_ms, labels=labels)
        self._collector.observe(
            self.M_RESULTS, float(outcome.result_count), labels=labels
        )
        if outcome.top_score is not None:
            self._collector.observe(
                self.M_TOP_SCORE, outcome.top_score, labels=labels
            )
        if outcome.result_count == 0:
            self._collector.increment(self.M_EMPTY, labels=labels)

    def record_latency(self, strategy: str, tenant_id: str, duration_ms: float) -> None:
        self._collector.observe(
            self.M_LATENCY, duration_ms,
            labels={"strategy": strategy, "tenant": tenant_id},
        )


__all__ = ["RetrievalMetrics", "RetrievalOutcome"]
