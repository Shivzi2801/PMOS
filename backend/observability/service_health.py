"""
PMOS Observability & Monitoring — Service Health Checks (S2.6)

Defines the :class:`HealthCheck` interface and concrete, no-external-service
implementations used to assess component and dependency health:

* :class:`CallableHealthCheck` — wraps an arbitrary probe function.
* :class:`MetricThresholdHealthCheck` — derives health from the live metrics
  snapshot (e.g. error rate, p95 latency), turning quantitative signals into a
  qualitative state.
* :class:`DependencyHealthCheck` — wraps a ping callable for an upstream
  dependency, mapping exceptions/timeouts to UNHEALTHY/UNKNOWN.

Each check is time-bounded and exception-isolated: a check that raises yields an
``UNKNOWN`` result rather than propagating, so one faulty probe never breaks the
aggregate health endpoint.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

from .health_status import HealthCheckResult, HealthState
from .metrics_snapshot import MetricsSnapshot


class HealthCheck(ABC):
    """A single named health probe."""

    def __init__(self, component: str, *, critical: bool = True) -> None:
        self._component = component
        self._critical = critical

    @property
    def component(self) -> str:
        return self._component

    @property
    def critical(self) -> bool:
        return self._critical

    @abstractmethod
    def _probe(self) -> HealthCheckResult:
        """Run the actual probe. May raise; the wrapper handles isolation."""

    def check(self, *, clock=time.time) -> HealthCheckResult:
        """Execute the probe with timing and exception isolation."""
        start = clock()
        try:
            result = self._probe()
        except Exception as exc:  # noqa: BLE001 - isolation is intentional
            return HealthCheckResult(
                component=self._component,
                state=HealthState.UNKNOWN,
                message=f"health check raised: {exc}",
                latency_ms=(clock() - start) * 1000.0,
                critical=self._critical,
            )
        # Backfill latency/critical if the probe omitted them.
        latency = result.latency_ms
        if latency is None:
            latency = (clock() - start) * 1000.0
        return HealthCheckResult(
            component=result.component or self._component,
            state=result.state,
            message=result.message,
            latency_ms=latency,
            checked_at=result.checked_at,
            critical=self._critical if result.critical is True else result.critical,
            details=result.details,
        )


class CallableHealthCheck(HealthCheck):
    """Adapts a plain callable returning a :class:`HealthState` or bool."""

    def __init__(
        self,
        component: str,
        probe: Callable[[], object],
        *,
        critical: bool = True,
    ) -> None:
        super().__init__(component, critical=critical)
        self._fn = probe

    def _probe(self) -> HealthCheckResult:
        outcome = self._fn()
        if isinstance(outcome, HealthCheckResult):
            return outcome
        if isinstance(outcome, HealthState):
            state = outcome
        elif isinstance(outcome, bool):
            state = HealthState.HEALTHY if outcome else HealthState.UNHEALTHY
        else:
            state = HealthState.UNKNOWN
        return HealthCheckResult(
            component=self._component, state=state, critical=self._critical
        )


class DependencyHealthCheck(HealthCheck):
    """Health of an upstream dependency via a ping callable.

    A successful ping → HEALTHY. A raised exception → UNHEALTHY. A ping
    exceeding ``degraded_after_ms`` (but succeeding) → DEGRADED.
    """

    def __init__(
        self,
        component: str,
        ping: Callable[[], object],
        *,
        critical: bool = True,
        degraded_after_ms: Optional[float] = None,
        clock=time.time,
    ) -> None:
        super().__init__(component, critical=critical)
        self._ping = ping
        self._degraded_after_ms = degraded_after_ms
        self._clock = clock

    def _probe(self) -> HealthCheckResult:
        start = self._clock()
        try:
            self._ping()
        except Exception as exc:  # noqa: BLE001
            return HealthCheckResult(
                component=self._component,
                state=HealthState.UNHEALTHY,
                message=f"dependency unreachable: {exc}",
                latency_ms=(self._clock() - start) * 1000.0,
                critical=self._critical,
            )
        latency_ms = (self._clock() - start) * 1000.0
        state = HealthState.HEALTHY
        message = None
        if self._degraded_after_ms is not None and latency_ms > self._degraded_after_ms:
            state = HealthState.DEGRADED
            message = f"slow dependency ({latency_ms:.0f}ms)"
        return HealthCheckResult(
            component=self._component,
            state=state,
            message=message,
            latency_ms=latency_ms,
            critical=self._critical,
        )


class MetricThresholdHealthCheck(HealthCheck):
    """Derives health from a metrics snapshot.

    Evaluates an error-rate condition and/or a latency-quantile condition for a
    component, producing DEGRADED/UNHEALTHY when thresholds are crossed. The
    snapshot is supplied lazily via a provider so the check always reads current
    state.
    """

    def __init__(
        self,
        component: str,
        snapshot_provider: Callable[[], MetricsSnapshot],
        *,
        total_metric: Optional[str] = None,
        error_metric: Optional[str] = None,
        error_rate_degraded: float = 0.05,
        error_rate_unhealthy: float = 0.20,
        latency_metric: Optional[str] = None,
        latency_quantile: float = 0.95,
        latency_degraded_ms: Optional[float] = None,
        latency_unhealthy_ms: Optional[float] = None,
        critical: bool = True,
    ) -> None:
        super().__init__(component, critical=critical)
        self._snapshot_provider = snapshot_provider
        self._total_metric = total_metric
        self._error_metric = error_metric
        self._error_rate_degraded = error_rate_degraded
        self._error_rate_unhealthy = error_rate_unhealthy
        self._latency_metric = latency_metric
        self._latency_quantile = latency_quantile
        self._latency_degraded_ms = latency_degraded_ms
        self._latency_unhealthy_ms = latency_unhealthy_ms

    def _probe(self) -> HealthCheckResult:
        snapshot = self._snapshot_provider()
        worst = HealthState.HEALTHY
        messages = []

        if self._total_metric and self._error_metric:
            total = snapshot.counter_total(self._total_metric)
            errors = snapshot.counter_total(self._error_metric)
            if total > 0:
                rate = errors / total
                if rate >= self._error_rate_unhealthy:
                    worst = HealthState.worst((worst, HealthState.UNHEALTHY))
                    messages.append(f"error rate {rate:.1%}")
                elif rate >= self._error_rate_degraded:
                    worst = HealthState.worst((worst, HealthState.DEGRADED))
                    messages.append(f"error rate {rate:.1%}")

        if self._latency_metric and (
            self._latency_degraded_ms is not None
            or self._latency_unhealthy_ms is not None
        ):
            # Aggregate the quantile across all label sets of the metric.
            qs = [
                s.histogram.quantile(self._latency_quantile)
                for s in snapshot.by_name(self._latency_metric)
                if s.histogram is not None and s.histogram.count > 0
            ]
            if qs:
                q_val = max(qs)
                if (
                    self._latency_unhealthy_ms is not None
                    and q_val >= self._latency_unhealthy_ms
                ):
                    worst = HealthState.worst((worst, HealthState.UNHEALTHY))
                    messages.append(f"p{int(self._latency_quantile*100)} {q_val:.0f}ms")
                elif (
                    self._latency_degraded_ms is not None
                    and q_val >= self._latency_degraded_ms
                ):
                    worst = HealthState.worst((worst, HealthState.DEGRADED))
                    messages.append(f"p{int(self._latency_quantile*100)} {q_val:.0f}ms")

        return HealthCheckResult(
            component=self._component,
            state=worst,
            message="; ".join(messages) if messages else None,
            critical=self._critical,
        )


__all__ = [
    "HealthCheck",
    "CallableHealthCheck",
    "DependencyHealthCheck",
    "MetricThresholdHealthCheck",
]
