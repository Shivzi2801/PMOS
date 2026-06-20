"""
PMOS Observability & Monitoring — Health Aggregator (S2.6)

Owns the set of registered :class:`HealthCheck` probes and combines their
results into a single :class:`HealthReport`. The aggregation rule respects the
``critical`` flag on each check:

* A non-HEALTHY *critical* component drives the overall state directly.
* A non-HEALTHY *non-critical* component caps the overall state at DEGRADED
  (never UNHEALTHY on its own), because losing a non-critical dependency
  degrades but does not fully break the platform.

Results power liveness/readiness probes (Kubernetes-style) and feed the alert
engine via emitted health telemetry events.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from .errors import UnknownComponentError
from .health_status import HealthCheckResult, HealthReport, HealthState
from .service_health import HealthCheck


class HealthAggregator:
    """Registry + evaluator for health checks."""

    def __init__(self, *, clock=time.time) -> None:
        self._lock = threading.RLock()
        self._checks: Dict[str, HealthCheck] = {}
        self._clock = clock
        self._last_report: Optional[HealthReport] = None

    # -- registration -----------------------------------------------------

    def register(self, check: HealthCheck) -> HealthCheck:
        with self._lock:
            self._checks[check.component] = check
            return check

    def unregister(self, component: str) -> None:
        with self._lock:
            if component not in self._checks:
                raise UnknownComponentError(component)
            del self._checks[component]

    def components(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._checks.keys())

    # -- evaluation -------------------------------------------------------

    def check_component(self, component: str) -> HealthCheckResult:
        with self._lock:
            check = self._checks.get(component)
        if check is None:
            raise UnknownComponentError(component)
        return check.check(clock=self._clock)

    def evaluate(self) -> HealthReport:
        """Run every check and aggregate into a report."""
        with self._lock:
            checks = list(self._checks.values())

        results: List[HealthCheckResult] = [c.check(clock=self._clock) for c in checks]
        overall = self._aggregate_state(results)
        report = HealthReport(
            state=overall,
            checked_at=self._clock(),
            components=tuple(results),
        )
        with self._lock:
            self._last_report = report
        return report

    @staticmethod
    def _aggregate_state(results: List[HealthCheckResult]) -> HealthState:
        if not results:
            return HealthState.HEALTHY

        critical_states = tuple(r.state for r in results if r.critical)
        noncritical_states = tuple(r.state for r in results if not r.critical)

        overall = HealthState.worst(critical_states) if critical_states else HealthState.HEALTHY

        # Non-critical failures cap at DEGRADED.
        if noncritical_states:
            nc_worst = HealthState.worst(noncritical_states)
            if nc_worst is not HealthState.HEALTHY:
                capped = (
                    HealthState.DEGRADED
                    if nc_worst in (HealthState.UNHEALTHY, HealthState.UNKNOWN)
                    else nc_worst
                )
                overall = HealthState.worst((overall, capped))
        return overall

    # -- cached views -----------------------------------------------------

    def last_report(self) -> Optional[HealthReport]:
        with self._lock:
            return self._last_report

    def readiness(self) -> HealthReport:
        """Evaluate and return a report for a readiness probe."""
        return self.evaluate()

    def liveness(self) -> bool:
        """Cheap liveness signal — does not run checks; returns True if the
        process can produce a report at all."""
        return True


__all__ = ["HealthAggregator"]
