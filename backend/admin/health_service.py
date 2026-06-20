"""Platform health: component & dependency checks, readiness/liveness probes.

Health checks are registered as named callables returning a
:class:`ComponentCheck`. The service distinguishes:

  * **components** — internal subsystems this process owns (e.g. config store);
  * **dependencies** — external systems we rely on (e.g. database, queue).

Aggregation policy:
  * any UNHEALTHY component or dependency => overall UNHEALTHY;
  * else any DEGRADED => overall DEGRADED;
  * else HEALTHY.

Liveness reflects only whether the process itself is responsive (always healthy
if this code runs). Readiness reflects whether the platform can serve traffic
(all *critical* dependencies healthy).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .metrics import HEALTH_STATUS, get_metrics
from .models import HealthStatus, PlatformHealth, utcnow


@dataclass
class ComponentCheck:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    detail: str = ""
    latency_ms: float = 0.0


CheckFn = Callable[[], ComponentCheck]

_STATUS_GAUGE = {
    HealthStatus.HEALTHY: 1.0,
    HealthStatus.DEGRADED: 0.5,
    HealthStatus.UNHEALTHY: 0.0,
    HealthStatus.UNKNOWN: -1.0,
}


class HealthService:
    """Register checks and compute platform health snapshots."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._component_checks: dict[str, CheckFn] = {}
        self._dependency_checks: dict[str, tuple[CheckFn, bool]] = {}  # fn, critical

    def register_component(self, name: str, check: CheckFn) -> None:
        with self._lock:
            self._component_checks[name] = check

    def register_dependency(
        self, name: str, check: CheckFn, *, critical: bool = True
    ) -> None:
        with self._lock:
            self._dependency_checks[name] = (check, critical)

    def _run(self, fn: CheckFn) -> ComponentCheck:
        start = time.perf_counter()
        try:
            result = fn()
        except Exception as exc:  # a throwing check is itself unhealthy
            elapsed = (time.perf_counter() - start) * 1000.0
            return ComponentCheck(
                name="unknown",
                status=HealthStatus.UNHEALTHY,
                detail=f"check raised: {exc}",
                latency_ms=elapsed,
            )
        if result.latency_ms == 0.0:
            result.latency_ms = (time.perf_counter() - start) * 1000.0
        return result

    def check_health(self) -> PlatformHealth:
        """Run all checks and aggregate an overall status."""
        components: dict[str, str] = {}
        dependencies: dict[str, str] = {}
        details: dict[str, object] = {}
        statuses: list[HealthStatus] = []

        with self._lock:
            comp_items = list(self._component_checks.items())
            dep_items = list(self._dependency_checks.items())

        for name, fn in comp_items:
            res = self._run(fn)
            components[name] = res.status.value
            details[f"component:{name}"] = {
                "detail": res.detail,
                "latency_ms": round(res.latency_ms, 3),
            }
            statuses.append(res.status)

        for name, (fn, _critical) in dep_items:
            res = self._run(fn)
            dependencies[name] = res.status.value
            details[f"dependency:{name}"] = {
                "detail": res.detail,
                "latency_ms": round(res.latency_ms, 3),
                "critical": _critical,
            }
            statuses.append(res.status)

        overall = self._aggregate(statuses)
        get_metrics().gauge(HEALTH_STATUS, _STATUS_GAUGE[overall])

        return PlatformHealth(
            status=overall,
            components=components,
            dependencies=dependencies,
            details=details,
            checked_at=utcnow(),
        )

    def liveness(self) -> dict[str, object]:
        """Liveness probe: is the process alive and able to execute code?"""
        return {"status": HealthStatus.HEALTHY.value, "checked_at": utcnow().isoformat()}

    def readiness(self) -> dict[str, object]:
        """Readiness probe: are all critical dependencies healthy?"""
        with self._lock:
            dep_items = list(self._dependency_checks.items())

        not_ready: list[str] = []
        results: dict[str, str] = {}
        for name, (fn, critical) in dep_items:
            res = self._run(fn)
            results[name] = res.status.value
            if critical and res.status != HealthStatus.HEALTHY:
                not_ready.append(name)

        ready = not not_ready
        return {
            "ready": ready,
            "status": HealthStatus.HEALTHY.value if ready else HealthStatus.UNHEALTHY.value,
            "dependencies": results,
            "not_ready": not_ready,
            "checked_at": utcnow().isoformat(),
        }

    def operational_status(self) -> dict[str, object]:
        """A combined operational summary for dashboards."""
        health = self.check_health()
        readiness = self.readiness()
        return {
            "health": health.to_dict(),
            "ready": readiness["ready"],
            "live": True,
        }

    @staticmethod
    def _aggregate(statuses: list[HealthStatus]) -> HealthStatus:
        if not statuses:
            return HealthStatus.HEALTHY
        if any(s == HealthStatus.UNHEALTHY for s in statuses):
            return HealthStatus.UNHEALTHY
        if any(s == HealthStatus.DEGRADED for s in statuses):
            return HealthStatus.DEGRADED
        if all(s == HealthStatus.HEALTHY for s in statuses):
            return HealthStatus.HEALTHY
        return HealthStatus.UNKNOWN
