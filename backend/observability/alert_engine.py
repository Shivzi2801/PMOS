"""
PMOS Observability & Monitoring — Alert Engine (S2.6)

Evaluates registered :class:`AlertRule` objects against the current metrics
snapshot and health report, manages alert *lifecycle* (pending → firing →
resolved), debounces transient breaches via ``for_consecutive``, and emits
telemetry events on state transitions.

State machine per rule
----------------------
    OK  --breach (n>=for_consecutive)-->  FIRING
    FIRING  --no breach-->  RESOLVED (then back to OK)

The engine is pull-based: a scheduler (or the observability service) calls
:meth:`evaluate` periodically. It holds no threads of its own, keeping it
testable and framework-agnostic.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from .alert_rule import AlertRule, AlertSeverity, ConditionResult
from .health_status import HealthReport
from .metrics_snapshot import MetricsSnapshot
from .telemetry_event import (
    EventCategory,
    EventContext,
    EventSeverity,
    TelemetryEvent,
)


class AlertState(str, Enum):
    OK = "ok"
    PENDING = "pending"
    FIRING = "firing"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class Alert:
    """An active or historical alert instance."""

    rule_name: str
    state: AlertState
    severity: AlertSeverity
    observed_value: Optional[float]
    threshold: Optional[float]
    detail: Optional[str]
    since: float
    updated_at: float
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.state is AlertState.FIRING

    def to_dict(self) -> Dict[str, object]:
        return {
            "rule": self.rule_name,
            "state": self.state.value,
            "severity": self.severity.value,
            "observed_value": self.observed_value,
            "threshold": self.threshold,
            "detail": self.detail,
            "since": self.since,
            "updated_at": self.updated_at,
            "labels": dict(self.labels),
        }


@dataclass
class _RuleRuntime:
    rule: AlertRule
    consecutive_breaches: int = 0
    state: AlertState = AlertState.OK
    since: float = 0.0


# Severity mapping for emitted telemetry.
_ALERT_TO_EVENT_SEVERITY = {
    AlertSeverity.INFO: EventSeverity.INFO,
    AlertSeverity.WARNING: EventSeverity.WARNING,
    AlertSeverity.CRITICAL: EventSeverity.CRITICAL,
}

AlertListener = Callable[[Alert], None]


class AlertEngine:
    """Registers rules and evaluates them on demand."""

    def __init__(
        self,
        *,
        event_emitter: Optional[Callable[[TelemetryEvent], None]] = None,
        listeners: Optional[List[AlertListener]] = None,
        clock=time.time,
    ) -> None:
        self._lock = threading.RLock()
        self._rules: Dict[str, _RuleRuntime] = {}
        self._event_emitter = event_emitter
        self._listeners: List[AlertListener] = list(listeners or [])
        self._clock = clock
        self._active: Dict[str, Alert] = {}

    # -- registration -----------------------------------------------------

    def register(self, rule: AlertRule) -> None:
        with self._lock:
            self._rules[rule.name] = _RuleRuntime(rule=rule)

    def register_many(self, rules: List[AlertRule]) -> None:
        for r in rules:
            self.register(r)

    def add_listener(self, listener: AlertListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def rules(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._rules.keys())

    # -- evaluation -------------------------------------------------------

    def evaluate(
        self,
        snapshot: MetricsSnapshot,
        health: Optional[HealthReport] = None,
    ) -> Tuple[Alert, ...]:
        """Evaluate all rules. Returns the alerts whose state changed."""
        now = self._clock()
        transitions: List[Alert] = []

        with self._lock:
            runtimes = list(self._rules.values())

        for rt in runtimes:
            try:
                result = rt.rule.condition.evaluate(snapshot, health)
            except Exception as exc:  # noqa: BLE001 - a bad rule cannot break others
                result = ConditionResult(False, detail=f"evaluation error: {exc}")

            transition = self._advance(rt, result, now)
            if transition is not None:
                transitions.append(transition)

        return tuple(transitions)

    def _advance(
        self, rt: _RuleRuntime, result: ConditionResult, now: float
    ) -> Optional[Alert]:
        prev_state = rt.state

        if result.breached:
            rt.consecutive_breaches += 1
            if rt.consecutive_breaches >= rt.rule.for_consecutive:
                if rt.state is not AlertState.FIRING:
                    rt.state = AlertState.FIRING
                    rt.since = now
                    alert = self._make_alert(rt, result, now, AlertState.FIRING)
                    with self._lock:
                        self._active[rt.rule.name] = alert
                    self._emit(alert)
                    return alert if prev_state is not AlertState.FIRING else None
            else:
                if rt.state is AlertState.OK:
                    rt.state = AlertState.PENDING
                    rt.since = now
        else:
            rt.consecutive_breaches = 0
            if rt.state is AlertState.FIRING:
                rt.state = AlertState.OK
                alert = self._make_alert(rt, result, now, AlertState.RESOLVED)
                with self._lock:
                    self._active.pop(rt.rule.name, None)
                self._emit(alert)
                return alert
            rt.state = AlertState.OK
        return None

    def _make_alert(
        self,
        rt: _RuleRuntime,
        result: ConditionResult,
        now: float,
        state: AlertState,
    ) -> Alert:
        return Alert(
            rule_name=rt.rule.name,
            state=state,
            severity=rt.rule.severity,
            observed_value=result.observed_value,
            threshold=result.threshold,
            detail=result.detail,
            since=rt.since or now,
            updated_at=now,
            labels=dict(rt.rule.labels),
        )

    # -- queries ----------------------------------------------------------

    def active_alerts(self) -> Tuple[Alert, ...]:
        with self._lock:
            return tuple(self._active.values())

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    # -- notification -----------------------------------------------------

    def _emit(self, alert: Alert) -> None:
        for listener in list(self._listeners):
            try:
                listener(alert)
            except Exception:  # noqa: BLE001
                pass
        if self._event_emitter is None:
            return
        name = (
            f"alert.fired" if alert.state is AlertState.FIRING else "alert.resolved"
        )
        event = TelemetryEvent(
            name=name,
            category=EventCategory.ALERT,
            severity=_ALERT_TO_EVENT_SEVERITY.get(alert.severity, EventSeverity.WARNING),
            timestamp=alert.updated_at,
            context=EventContext(component="alert_engine"),
            attributes={
                "rule": alert.rule_name,
                "state": alert.state.value,
                "observed_value": alert.observed_value,
                "threshold": alert.threshold,
                "detail": alert.detail,
                **dict(alert.labels),
            },
        )
        try:
            self._event_emitter(event)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["AlertState", "Alert", "AlertEngine", "AlertListener"]
