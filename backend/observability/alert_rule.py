"""
PMOS Observability & Monitoring — Alert Rule (S2.6)

Declarative alert rule definitions plus the conditions they evaluate against a
:class:`MetricsSnapshot` and/or :class:`HealthReport`. Rules are pure, immutable
descriptions; the :class:`~pmos.observability.alert_engine.AlertEngine` does the
evaluating and state-tracking (firing/resolved, deduplication, etc.).

Supported condition families
----------------------------
* :class:`CounterRateCondition`     — error/failure thresholds derived from
  counter ratios (e.g. error_rate >= 5%).
* :class:`LatencyQuantileCondition` — SLA latency thresholds (e.g. p95 >= 2s).
* :class:`GaugeThresholdCondition`  — gauge crossing (e.g. in-flight >= 1000).
* :class:`HealthCondition`          — service health (e.g. any critical
  component UNHEALTHY).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple

from .errors import InvalidAlertRuleError
from .health_status import HealthReport, HealthState
from .metrics_snapshot import MetricsSnapshot


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ComparisonOp(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"

    def compare(self, value: float, threshold: float) -> bool:
        if self is ComparisonOp.GT:
            return value > threshold
        if self is ComparisonOp.GTE:
            return value >= threshold
        if self is ComparisonOp.LT:
            return value < threshold
        return value <= threshold


@dataclass(frozen=True)
class ConditionResult:
    """Outcome of evaluating a condition once."""

    breached: bool
    observed_value: Optional[float] = None
    threshold: Optional[float] = None
    detail: Optional[str] = None


class AlertCondition(ABC):
    """A predicate over the current observability state."""

    @abstractmethod
    def evaluate(
        self,
        snapshot: MetricsSnapshot,
        health: Optional[HealthReport] = None,
    ) -> ConditionResult:
        ...


@dataclass(frozen=True)
class CounterRateCondition(AlertCondition):
    """Breaches when numerator/denominator counter ratio crosses a threshold.

    Used for error-rate and workflow-failure-rate alerts. If the denominator is
    zero (no traffic) the condition does not breach.
    """

    numerator_metric: str
    denominator_metric: str
    op: ComparisonOp
    threshold: float
    min_samples: float = 1.0

    def evaluate(self, snapshot, health=None) -> ConditionResult:
        denom = snapshot.counter_total(self.denominator_metric)
        if denom < self.min_samples:
            return ConditionResult(False, detail="insufficient samples")
        num = snapshot.counter_total(self.numerator_metric)
        rate = num / denom if denom else 0.0
        return ConditionResult(
            breached=self.op.compare(rate, self.threshold),
            observed_value=rate,
            threshold=self.threshold,
            detail=f"{num:.0f}/{denom:.0f}={rate:.3f}",
        )


@dataclass(frozen=True)
class LatencyQuantileCondition(AlertCondition):
    """Breaches when the worst per-label quantile of a histogram crosses a
    latency threshold. Powers SLA latency alerts."""

    metric: str
    quantile: float
    op: ComparisonOp
    threshold_ms: float

    def evaluate(self, snapshot, health=None) -> ConditionResult:
        quantiles = [
            s.histogram.quantile(self.quantile)
            for s in snapshot.by_name(self.metric)
            if s.histogram is not None and s.histogram.count > 0
        ]
        if not quantiles:
            return ConditionResult(False, detail="no observations")
        worst = max(quantiles)
        return ConditionResult(
            breached=self.op.compare(worst, self.threshold_ms),
            observed_value=worst,
            threshold=self.threshold_ms,
            detail=f"p{int(self.quantile*100)}={worst:.0f}ms",
        )


@dataclass(frozen=True)
class GaugeThresholdCondition(AlertCondition):
    """Breaches when the max gauge value across label sets crosses a threshold."""

    metric: str
    op: ComparisonOp
    threshold: float

    def evaluate(self, snapshot, health=None) -> ConditionResult:
        values = [s.value for s in snapshot.by_name(self.metric) if s.value is not None]
        if not values:
            return ConditionResult(False, detail="no value")
        observed = max(values)
        return ConditionResult(
            breached=self.op.compare(observed, self.threshold),
            observed_value=observed,
            threshold=self.threshold,
        )


@dataclass(frozen=True)
class HealthCondition(AlertCondition):
    """Breaches when overall health (or a named component) reaches a state at or
    worse than ``min_state``."""

    min_state: HealthState = HealthState.UNHEALTHY
    component: Optional[str] = None

    def evaluate(self, snapshot, health=None) -> ConditionResult:
        if health is None:
            return ConditionResult(False, detail="no health report")
        if self.component is not None:
            result = health.component(self.component)
            if result is None:
                return ConditionResult(False, detail="component absent")
            state = result.state
        else:
            state = health.state
        breached = state.severity >= self.min_state.severity
        return ConditionResult(
            breached=breached,
            detail=f"state={state.value}",
        )


@dataclass(frozen=True)
class AlertRule:
    """A named, immutable alert rule.

    Attributes
    ----------
    name:
        Unique rule identifier.
    condition:
        The :class:`AlertCondition` to evaluate.
    severity:
        Severity assigned to alerts this rule produces.
    description:
        Human-readable explanation.
    for_consecutive:
        Number of consecutive breached evaluations required before the alert
        fires (debounce against transient spikes). Default 1 = fire immediately.
    labels:
        Static labels attached to produced alerts (e.g. team, runbook).
    """

    name: str
    condition: AlertCondition
    severity: AlertSeverity = AlertSeverity.WARNING
    description: str = ""
    for_consecutive: int = 1
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise InvalidAlertRuleError("Alert rule requires a name")
        if self.for_consecutive < 1:
            raise InvalidAlertRuleError(
                "for_consecutive must be >= 1",
                details={"rule": self.name},
            )


__all__ = [
    "AlertSeverity",
    "ComparisonOp",
    "ConditionResult",
    "AlertCondition",
    "CounterRateCondition",
    "LatencyQuantileCondition",
    "GaugeThresholdCondition",
    "HealthCondition",
    "AlertRule",
]
