"""
PMOS Observability & Monitoring — SLA Monitor (S2.6)

Tracks Service Level Objectives and computes attainment + error budget from the
metrics snapshot. SLOs here are expressed as:

* **Availability SLO** — success_ratio over a window must stay >= target
  (e.g. 99.5% of API requests non-5xx).
* **Latency SLO** — a chosen quantile must stay <= a latency budget
  (e.g. p95 generation latency <= 8s).

The monitor derives, for each SLO, the current attainment, whether it is in
violation, and how much error budget remains. It also produces ready-made
:class:`AlertRule` objects so SLOs and alerts stay in sync from one definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from .alert_rule import (
    AlertRule,
    AlertSeverity,
    ComparisonOp,
    CounterRateCondition,
    LatencyQuantileCondition,
)
from .metrics_snapshot import MetricsSnapshot


class SloKind(str, Enum):
    AVAILABILITY = "availability"
    LATENCY = "latency"


@dataclass(frozen=True)
class AvailabilitySlo:
    """Success-ratio SLO defined via total + error counters."""

    name: str
    total_metric: str
    error_metric: str
    target: float  # e.g. 0.995
    severity: AlertSeverity = AlertSeverity.CRITICAL

    kind: SloKind = field(default=SloKind.AVAILABILITY, init=False)

    def to_alert_rule(self) -> AlertRule:
        # Fire when error rate exceeds the allowed budget (1 - target).
        return AlertRule(
            name=f"slo:{self.name}",
            condition=CounterRateCondition(
                numerator_metric=self.error_metric,
                denominator_metric=self.total_metric,
                op=ComparisonOp.GT,
                threshold=1.0 - self.target,
            ),
            severity=self.severity,
            description=f"Availability SLO '{self.name}' below {self.target:.3%}",
            labels={"slo": self.name, "kind": self.kind.value},
        )


@dataclass(frozen=True)
class LatencySlo:
    """Latency-quantile SLO."""

    name: str
    latency_metric: str
    quantile: float
    budget_ms: float
    severity: AlertSeverity = AlertSeverity.WARNING

    kind: SloKind = field(default=SloKind.LATENCY, init=False)

    def to_alert_rule(self) -> AlertRule:
        return AlertRule(
            name=f"slo:{self.name}",
            condition=LatencyQuantileCondition(
                metric=self.latency_metric,
                quantile=self.quantile,
                op=ComparisonOp.GT,
                threshold_ms=self.budget_ms,
            ),
            severity=self.severity,
            description=(
                f"Latency SLO '{self.name}' p{int(self.quantile*100)} "
                f"over {self.budget_ms:.0f}ms"
            ),
            labels={"slo": self.name, "kind": self.kind.value},
        )


@dataclass(frozen=True)
class SloStatus:
    name: str
    kind: SloKind
    target: float
    attainment: float
    in_violation: bool
    error_budget_remaining: Optional[float]
    detail: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "target": self.target,
            "attainment": self.attainment,
            "in_violation": self.in_violation,
            "error_budget_remaining": self.error_budget_remaining,
            "detail": self.detail,
        }


class SlaMonitor:
    """Holds SLO definitions and evaluates them against snapshots."""

    def __init__(self) -> None:
        self._availability: List[AvailabilitySlo] = []
        self._latency: List[LatencySlo] = []

    def add_availability(self, slo: AvailabilitySlo) -> None:
        self._availability.append(slo)

    def add_latency(self, slo: LatencySlo) -> None:
        self._latency.append(slo)

    def alert_rules(self) -> Tuple[AlertRule, ...]:
        """Generate alert rules covering all SLOs (for the alert engine)."""
        rules = [s.to_alert_rule() for s in self._availability]
        rules += [s.to_alert_rule() for s in self._latency]
        return tuple(rules)

    def evaluate(self, snapshot: MetricsSnapshot) -> Tuple[SloStatus, ...]:
        statuses: List[SloStatus] = []

        for slo in self._availability:
            total = snapshot.counter_total(slo.total_metric)
            errors = snapshot.counter_total(slo.error_metric)
            if total <= 0:
                attainment = 1.0
                budget = 1.0
                detail = "no traffic"
                violation = False
            else:
                attainment = 1.0 - (errors / total)
                allowed = 1.0 - slo.target
                consumed = (errors / total) / allowed if allowed > 0 else 0.0
                budget = max(0.0, 1.0 - consumed)
                violation = attainment < slo.target
                detail = f"{errors:.0f}/{total:.0f} errors"
            statuses.append(
                SloStatus(
                    name=slo.name,
                    kind=SloKind.AVAILABILITY,
                    target=slo.target,
                    attainment=attainment,
                    in_violation=violation,
                    error_budget_remaining=budget,
                    detail=detail,
                )
            )

        for slo in self._latency:
            qs = [
                s.histogram.quantile(slo.quantile)
                for s in snapshot.by_name(slo.latency_metric)
                if s.histogram is not None and s.histogram.count > 0
            ]
            if not qs:
                statuses.append(
                    SloStatus(
                        name=slo.name,
                        kind=SloKind.LATENCY,
                        target=slo.budget_ms,
                        attainment=1.0,
                        in_violation=False,
                        error_budget_remaining=None,
                        detail="no observations",
                    )
                )
                continue
            worst = max(qs)
            violation = worst > slo.budget_ms
            statuses.append(
                SloStatus(
                    name=slo.name,
                    kind=SloKind.LATENCY,
                    target=slo.budget_ms,
                    attainment=min(1.0, slo.budget_ms / worst) if worst > 0 else 1.0,
                    in_violation=violation,
                    error_budget_remaining=None,
                    detail=f"p{int(slo.quantile*100)}={worst:.0f}ms",
                )
            )

        return tuple(statuses)

    def violations(self, snapshot: MetricsSnapshot) -> Tuple[SloStatus, ...]:
        return tuple(s for s in self.evaluate(snapshot) if s.in_violation)


__all__ = [
    "SloKind",
    "AvailabilitySlo",
    "LatencySlo",
    "SloStatus",
    "SlaMonitor",
]
