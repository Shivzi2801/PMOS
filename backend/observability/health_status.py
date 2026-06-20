"""
PMOS Observability & Monitoring — Health Status (S2.6)

Core value types for health monitoring: the tri-state :class:`HealthState`, an
individual :class:`HealthCheckResult`, and the aggregate :class:`HealthReport`.
These are pure data — the logic that produces them lives in
:mod:`service_health` and :mod:`health_aggregator`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple


class HealthState(str, Enum):
    """Tri-state health, ordered by severity for aggregation.

    HEALTHY   — fully operational.
    DEGRADED  — operational with reduced capacity/quality (e.g. a non-critical
                dependency is down, latency elevated).
    UNHEALTHY — not operational.
    UNKNOWN   — health could not be determined (check errored/timed out).
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

    @property
    def severity(self) -> int:
        return _STATE_SEVERITY[self]

    @classmethod
    def worst(cls, states: "Tuple[HealthState, ...]") -> "HealthState":
        if not states:
            return cls.UNKNOWN
        return max(states, key=lambda s: s.severity)


# Higher number == worse. UNKNOWN sits between degraded and unhealthy so an
# unmeasurable component is treated conservatively but not as a hard outage.
_STATE_SEVERITY: Dict["HealthState", int] = {
    HealthState.HEALTHY: 0,
    HealthState.DEGRADED: 1,
    HealthState.UNKNOWN: 2,
    HealthState.UNHEALTHY: 3,
}


@dataclass(frozen=True)
class HealthCheckResult:
    """Outcome of a single component/dependency health check."""

    component: str
    state: HealthState
    message: Optional[str] = None
    latency_ms: Optional[float] = None
    checked_at: float = field(default_factory=time.time)
    critical: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component,
            "state": self.state.value,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
            "critical": self.critical,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class HealthReport:
    """Aggregate health across all registered components."""

    state: HealthState
    checked_at: float
    components: Tuple[HealthCheckResult, ...] = field(default_factory=tuple)

    @property
    def is_ready(self) -> bool:
        """Readiness: serve traffic only when not UNHEALTHY."""
        return self.state in (HealthState.HEALTHY, HealthState.DEGRADED)

    @property
    def is_live(self) -> bool:
        """Liveness: the process itself is running. A report existing at all
        implies the aggregator executed, so liveness is true unless every
        component is UNKNOWN (interpreted as the process being wedged)."""
        if not self.components:
            return True
        return any(c.state is not HealthState.UNKNOWN for c in self.components)

    def component(self, name: str) -> Optional[HealthCheckResult]:
        for c in self.components:
            if c.component == name:
                return c
        return None

    def unhealthy_components(self) -> Tuple[HealthCheckResult, ...]:
        return tuple(c for c in self.components if c.state is HealthState.UNHEALTHY)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "ready": self.is_ready,
            "live": self.is_live,
            "checked_at": self.checked_at,
            "components": [c.to_dict() for c in self.components],
        }


__all__ = ["HealthState", "HealthCheckResult", "HealthReport"]
