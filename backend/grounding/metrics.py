"""
metrics.py
==========

Observability for the grounding layer.

WHY THIS FILE EXISTS
--------------------
You cannot operate a trust layer you cannot see. This module collects the
operational metrics the slice promises: how many answers were verified,
rejected, the distribution of confidence, hallucination rate, and latency.

It is backend-agnostic: it accumulates counters/histograms in memory and
exposes a snapshot. In production an exporter (Prometheus, StatsD, OTEL) reads
the snapshot or the recorder is subclassed to forward each event. Keeping the
interface tiny means wiring it to any telemetry system is trivial.

METRICS PRODUCED
----------------
* grounding_requests              (counter)
* verified_answers                (counter)
* partially_verified_answers      (counter)
* rejected_answers                (counter)
* citation_coverage               (running aggregate)
* hallucination_rate              (derived)
* confidence_distribution         (histogram buckets)
* verification_latency            (running aggregate, seconds)

DESIGN DECISIONS
----------------
* Thread-safe via a single lock; grounding is I/O-light so contention is
  negligible and correctness beats micro-optimisation here.
* Snapshots are plain dicts so they serialise straight to JSON for dashboards.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .grounding_result import HallucinationRisk, VerificationStatus


# Confidence histogram bucket upper bounds (inclusive).
_CONFIDENCE_BUCKETS = [0.2, 0.4, 0.6, 0.8, 1.0]


@dataclass
class _Aggregate:
    """Running sum/count for computing a mean without storing every sample."""

    count: int = 0
    total: float = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value

    @property
    def mean(self) -> float:
        return round(self.total / self.count, 4) if self.count else 0.0


class GroundingMetrics:
    """Thread-safe in-memory metrics recorder."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.grounding_requests = 0
        self.verified_answers = 0
        self.partially_verified_answers = 0
        self.rejected_answers = 0
        self._high_risk_answers = 0
        self._coverage = _Aggregate()
        self._latency = _Aggregate()
        self._confidence_hist: dict[str, int] = {
            self._bucket_label(b): 0 for b in _CONFIDENCE_BUCKETS
        }

    # ------------------------------------------------------------------ #
    def record(
        self,
        *,
        status: VerificationStatus,
        hallucination_risk: HallucinationRisk,
        citation_coverage: float,
        confidence_score: float,
        latency_seconds: float,
    ) -> None:
        with self._lock:
            self.grounding_requests += 1
            if status is VerificationStatus.VERIFIED:
                self.verified_answers += 1
            elif status is VerificationStatus.PARTIALLY_VERIFIED:
                self.partially_verified_answers += 1
            elif status is VerificationStatus.REJECTED:
                self.rejected_answers += 1

            if hallucination_risk is HallucinationRisk.HIGH:
                self._high_risk_answers += 1

            self._coverage.add(citation_coverage)
            self._latency.add(latency_seconds)
            self._confidence_hist[self._bucket_for(confidence_score)] += 1

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = self.grounding_requests
            hallucination_rate = (
                round(self._high_risk_answers / requests, 4) if requests else 0.0
            )
            return {
                "grounding_requests": requests,
                "verified_answers": self.verified_answers,
                "partially_verified_answers": self.partially_verified_answers,
                "rejected_answers": self.rejected_answers,
                "citation_coverage": self._coverage.mean,
                "hallucination_rate": hallucination_rate,
                "confidence_distribution": dict(self._confidence_hist),
                "verification_latency": self._latency.mean,
            }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _bucket_label(upper: float) -> str:
        return f"<= {upper:.1f}"

    def _bucket_for(self, value: float) -> str:
        for b in _CONFIDENCE_BUCKETS:
            if value <= b:
                return self._bucket_label(b)
        return self._bucket_label(_CONFIDENCE_BUCKETS[-1])
