"""
PMOS Observability & Monitoring — API Metrics (S2.6)

Typed facade for the API Layer slice (S2.1). Records request counts, latency,
errors, and in-flight concurrency. Designed to be driven from a single
middleware: ``on_request_start`` / ``on_request_end`` bracket each request and
keep the in-flight gauge accurate even on exceptions.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator, Optional

from .metrics_collector import MetricsCollector


class ApiMetrics:
    M_REQUESTS = "pmos.api.requests_total"
    M_LATENCY = "pmos.api.request_latency_ms"
    M_ERRORS = "pmos.api.errors_total"
    M_IN_FLIGHT = "pmos.api.in_flight"

    def __init__(self, collector: MetricsCollector, *, clock=time.time) -> None:
        self._collector = collector
        self._clock = clock

    def record_request(
        self,
        *,
        method: str,
        route: str,
        status: int,
        tenant_id: str,
        duration_ms: float,
        error_type: Optional[str] = None,
    ) -> None:
        self._collector.increment(
            self.M_REQUESTS,
            labels={
                "method": method,
                "route": route,
                "status": str(status),
                "tenant": tenant_id,
            },
        )
        self._collector.observe(
            self.M_LATENCY, duration_ms,
            labels={"method": method, "route": route, "tenant": tenant_id},
        )
        if status >= 500 or error_type:
            self._collector.increment(
                self.M_ERRORS,
                labels={
                    "route": route,
                    "error_type": error_type or f"http_{status}",
                    "tenant": tenant_id,
                },
            )

    def inc_in_flight(self, tenant_id: str) -> None:
        self._collector.adjust_gauge(self.M_IN_FLIGHT, 1.0, labels={"tenant": tenant_id})

    def dec_in_flight(self, tenant_id: str) -> None:
        self._collector.adjust_gauge(self.M_IN_FLIGHT, -1.0, labels={"tenant": tenant_id})

    @contextmanager
    def track_request(
        self, *, method: str, route: str, tenant_id: str
    ) -> Iterator["RequestTracker"]:
        """Context manager bracketing one request. Defaults status to 200 and
        flips to 500 on an uncaught exception."""
        self.inc_in_flight(tenant_id)
        start = self._clock()
        tracker = RequestTracker()
        try:
            yield tracker
        except BaseException as exc:  # noqa: BLE001
            tracker.status = tracker.status if tracker.status >= 400 else 500
            tracker.error_type = tracker.error_type or type(exc).__name__
            raise
        finally:
            duration_ms = (self._clock() - start) * 1000.0
            self.dec_in_flight(tenant_id)
            self.record_request(
                method=method,
                route=route,
                status=tracker.status,
                tenant_id=tenant_id,
                duration_ms=duration_ms,
                error_type=tracker.error_type,
            )


class RequestTracker:
    """Mutable holder used inside :meth:`ApiMetrics.track_request`."""

    __slots__ = ("status", "error_type")

    def __init__(self) -> None:
        self.status: int = 200
        self.error_type: Optional[str] = None

    def set_status(self, status: int) -> None:
        self.status = status


__all__ = ["ApiMetrics", "RequestTracker"]
