"""
api_metrics.py
==============

In-process observability registry.

Why this file exists
--------------------
The spec requires the API to expose operational signals: request_count,
error_count, latency, endpoint_usage, success_rate, rate_limit_events. These
are recorded by middleware (timing, errors) and the rate limiter (limit
events), then surfaced at ``GET /api/v1/metrics``.

Design
------
* A single thread-safe ``MetricsRegistry`` accumulates counters and a bounded
  latency window per endpoint.
* It is deliberately backend-agnostic. The same record/observe calls could be
  pointed at Prometheus or OpenTelemetry later without changing call sites.
* Latency percentiles are computed over a rolling, capped sample to bound
  memory.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Deque, Dict


class MetricsRegistry:
    """Thread-safe accumulator for API operational metrics."""

    def __init__(self, latency_window: int = 1000) -> None:
        self._lock = threading.Lock()
        self._request_count = 0
        self._error_count = 0
        self._rate_limit_events = 0
        self._endpoint_usage: Dict[str, int] = defaultdict(int)
        self._endpoint_errors: Dict[str, int] = defaultdict(int)
        self._latencies: Deque[float] = deque(maxlen=latency_window)
        self._latency_window = latency_window

    # ------------------------------------------------------------------ #
    # Recording (called by middleware / rate limiter)
    # ------------------------------------------------------------------ #
    def record_request(self, endpoint: str, latency_ms: float, is_error: bool) -> None:
        with self._lock:
            self._request_count += 1
            self._endpoint_usage[endpoint] += 1
            self._latencies.append(latency_ms)
            if is_error:
                self._error_count += 1
                self._endpoint_errors[endpoint] += 1

    def record_rate_limit_event(self) -> None:
        with self._lock:
            self._rate_limit_events += 1

    # ------------------------------------------------------------------ #
    # Reporting (called by the metrics endpoint)
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        with self._lock:
            total = self._request_count
            errors = self._error_count
            success_rate = (
                round((total - errors) / total, 4) if total else 1.0
            )
            latencies = sorted(self._latencies)
            return {
                "request_count": total,
                "error_count": errors,
                "success_rate": success_rate,
                "rate_limit_events": self._rate_limit_events,
                "endpoint_usage": dict(self._endpoint_usage),
                "latency_ms": self._compute_latency(latencies),
            }

    @staticmethod
    def _compute_latency(sorted_latencies) -> dict:
        if not sorted_latencies:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "count": 0}

        def pct(p: float) -> float:
            if not sorted_latencies:
                return 0.0
            idx = min(
                len(sorted_latencies) - 1,
                int(round((p / 100.0) * (len(sorted_latencies) - 1))),
            )
            return round(sorted_latencies[idx], 3)

        avg = round(sum(sorted_latencies) / len(sorted_latencies), 3)
        return {
            "p50": pct(50),
            "p95": pct(95),
            "p99": pct(99),
            "avg": avg,
            "count": len(sorted_latencies),
        }

    def reset(self) -> None:
        with self._lock:
            self._request_count = 0
            self._error_count = 0
            self._rate_limit_events = 0
            self._endpoint_usage.clear()
            self._endpoint_errors.clear()
            self._latencies.clear()


# Process-wide registry instance.
_REGISTRY = MetricsRegistry()


def get_metrics_registry() -> MetricsRegistry:
    return _REGISTRY
