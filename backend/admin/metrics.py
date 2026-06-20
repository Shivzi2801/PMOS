"""Observability for the admin module.

A lightweight, dependency-free metrics façade. In production this is backed by
Prometheus/OpenTelemetry; here it exposes the same counter/gauge/histogram
contract over an in-process registry so the module is fully testable and has no
hard dependency on a metrics backend. A real backend is injected by replacing
:data:`METRICS` with an adapter that implements :class:`MetricsSink`.

Tracked signals (per requirements):
    * admin actions          -> ``admin_actions_total``
    * configuration changes  -> ``admin_config_changes_total``
    * policy evaluations     -> ``admin_policy_evaluations_total``
    * quota violations       -> ``admin_quota_violations_total``
    * governance violations  -> ``admin_governance_violations_total``
    * tenant activity        -> ``admin_tenant_activity_total``
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Iterable, Mapping, Protocol


def _label_key(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


class MetricsSink(Protocol):
    """Contract any metrics backend must satisfy."""

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None: ...
    def gauge(self, name: str, value: float, **labels: str) -> None: ...
    def observe(self, name: str, value: float, **labels: str) -> None: ...


class InMemoryMetrics:
    """Thread-safe in-process metrics registry.

    Suitable for tests and single-process deployments; provides snapshotting so
    tests can assert on emitted metrics deterministically.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple], float] = {}
        self._observations: dict[tuple[str, tuple], list[float]] = defaultdict(list)

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._counters[(name, _label_key(labels))] += value

    def gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauges[(name, _label_key(labels))] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._observations[(name, _label_key(labels))].append(value)

    # -- introspection helpers (test / debug) ------------------------------ #
    def counter_value(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters.get((name, _label_key(labels)), 0.0)

    def gauge_value(self, name: str, **labels: str) -> float | None:
        with self._lock:
            return self._gauges.get((name, _label_key(labels)))

    def observations(self, name: str, **labels: str) -> list[float]:
        with self._lock:
            return list(self._observations.get((name, _label_key(labels)), []))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {
                    f"{n}{dict(l)}": v for (n, l), v in self._counters.items()
                },
                "gauges": {
                    f"{n}{dict(l)}": v for (n, l), v in self._gauges.items()
                },
                "histograms": {
                    f"{n}{dict(l)}": list(v)
                    for (n, l), v in self._observations.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._observations.clear()


# Canonical metric names ----------------------------------------------------- #
ADMIN_ACTIONS = "admin_actions_total"
CONFIG_CHANGES = "admin_config_changes_total"
POLICY_EVALUATIONS = "admin_policy_evaluations_total"
QUOTA_VIOLATIONS = "admin_quota_violations_total"
QUOTA_CHECKS = "admin_quota_checks_total"
GOVERNANCE_VIOLATIONS = "admin_governance_violations_total"
TENANT_ACTIVITY = "admin_tenant_activity_total"
WORKSPACE_ACTIVITY = "admin_workspace_activity_total"
FEATURE_FLAG_EVALUATIONS = "admin_feature_flag_evaluations_total"
AUDIT_EVENTS = "admin_audit_events_total"
HEALTH_STATUS = "admin_health_status"  # gauge: 1 healthy, 0.5 degraded, 0 unhealthy


# Module-level default sink. Replace to integrate a real backend.
METRICS: MetricsSink = InMemoryMetrics()


def set_metrics_sink(sink: MetricsSink) -> None:
    """Install a custom metrics backend (e.g. a Prometheus adapter)."""
    global METRICS
    METRICS = sink


def get_metrics() -> MetricsSink:
    return METRICS
