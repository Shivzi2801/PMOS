"""
PMOS Observability & Monitoring — Dashboard Projection (S2.6)

Transforms raw observability state (metrics snapshot, health report, active
alerts, SLO statuses, recent telemetry) into a compact, serializable
projection optimized for operational dashboards. The projection is a read-model:
it does no collection of its own, only summarization, so it is cheap to build on
demand for an admin UI or status endpoint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .alert_engine import Alert
from .health_status import HealthReport, HealthState
from .metrics_snapshot import MetricsSnapshot
from .sla_monitor import SloStatus


@dataclass(frozen=True)
class LatencyPanel:
    metric: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    count: int
    max_ms: float


@dataclass(frozen=True)
class ThroughputPanel:
    metric: str
    total: float


@dataclass(frozen=True)
class DashboardProjection:
    generated_at: float
    health_state: str
    ready: bool
    live: bool
    active_alert_count: int
    critical_alert_count: int
    slo_violations: int
    latency_panels: Tuple[LatencyPanel, ...] = field(default_factory=tuple)
    throughput_panels: Tuple[ThroughputPanel, ...] = field(default_factory=tuple)
    component_states: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    alerts: Tuple[Dict[str, object], ...] = field(default_factory=tuple)
    slos: Tuple[Dict[str, object], ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "health": {
                "state": self.health_state,
                "ready": self.ready,
                "live": self.live,
                "components": [
                    {"component": c, "state": s} for c, s in self.component_states
                ],
            },
            "alerts": {
                "active": self.active_alert_count,
                "critical": self.critical_alert_count,
                "items": list(self.alerts),
            },
            "slo": {
                "violations": self.slo_violations,
                "items": list(self.slos),
            },
            "latency": [
                {
                    "metric": p.metric,
                    "p50_ms": round(p.p50_ms, 2),
                    "p95_ms": round(p.p95_ms, 2),
                    "p99_ms": round(p.p99_ms, 2),
                    "max_ms": round(p.max_ms, 2),
                    "count": p.count,
                }
                for p in self.latency_panels
            ],
            "throughput": [
                {"metric": p.metric, "total": p.total} for p in self.throughput_panels
            ],
        }


# Default set of latency histograms and throughput counters surfaced on the
# operational dashboard. Deployments may override via the builder.
DEFAULT_LATENCY_METRICS: Tuple[str, ...] = (
    "pmos.api.request_latency_ms",
    "pmos.workflow.duration_ms",
    "pmos.retrieval.latency_ms",
    "pmos.generation.latency_ms",
    "pmos.grounding.latency_ms",
)

DEFAULT_THROUGHPUT_METRICS: Tuple[str, ...] = (
    "pmos.api.requests_total",
    "pmos.workflow.runs_total",
    "pmos.generation.runs_total",
    "pmos.ingestion.documents_total",
)


class DashboardProjectionBuilder:
    """Builds a :class:`DashboardProjection` from current observability state."""

    def __init__(
        self,
        *,
        latency_metrics: Tuple[str, ...] = DEFAULT_LATENCY_METRICS,
        throughput_metrics: Tuple[str, ...] = DEFAULT_THROUGHPUT_METRICS,
        clock=time.time,
    ) -> None:
        self._latency_metrics = latency_metrics
        self._throughput_metrics = throughput_metrics
        self._clock = clock

    def build(
        self,
        snapshot: MetricsSnapshot,
        *,
        health: Optional[HealthReport] = None,
        alerts: Tuple[Alert, ...] = (),
        slos: Tuple[SloStatus, ...] = (),
    ) -> DashboardProjection:
        latency_panels = self._build_latency_panels(snapshot)
        throughput_panels = self._build_throughput_panels(snapshot)

        if health is not None:
            health_state = health.state.value
            ready = health.is_ready
            live = health.is_live
            component_states = tuple(
                (c.component, c.state.value) for c in health.components
            )
        else:
            health_state = HealthState.UNKNOWN.value
            ready = False
            live = True
            component_states = ()

        critical = sum(1 for a in alerts if a.severity.value == "critical")
        slo_violations = sum(1 for s in slos if s.in_violation)

        return DashboardProjection(
            generated_at=self._clock(),
            health_state=health_state,
            ready=ready,
            live=live,
            active_alert_count=len(alerts),
            critical_alert_count=critical,
            slo_violations=slo_violations,
            latency_panels=latency_panels,
            throughput_panels=throughput_panels,
            component_states=component_states,
            alerts=tuple(a.to_dict() for a in alerts),
            slos=tuple(s.to_dict() for s in slos),
        )

    def _build_latency_panels(self, snapshot: MetricsSnapshot) -> Tuple[LatencyPanel, ...]:
        panels: List[LatencyPanel] = []
        for metric in self._latency_metrics:
            samples = [s for s in snapshot.by_name(metric) if s.histogram]
            if not samples:
                continue
            # Aggregate across label sets by merging counts via re-derived
            # quantiles on each then taking the worst, plus summed counts.
            count = sum(s.histogram.count for s in samples)
            if count == 0:
                continue
            p50 = max(s.histogram.quantile(0.50) for s in samples)
            p95 = max(s.histogram.quantile(0.95) for s in samples)
            p99 = max(s.histogram.quantile(0.99) for s in samples)
            max_ms = max((s.histogram.max or 0.0) for s in samples)
            panels.append(
                LatencyPanel(
                    metric=metric,
                    p50_ms=p50,
                    p95_ms=p95,
                    p99_ms=p99,
                    count=count,
                    max_ms=max_ms,
                )
            )
        return tuple(panels)

    def _build_throughput_panels(
        self, snapshot: MetricsSnapshot
    ) -> Tuple[ThroughputPanel, ...]:
        panels: List[ThroughputPanel] = []
        for metric in self._throughput_metrics:
            total = snapshot.counter_total(metric)
            if total > 0:
                panels.append(ThroughputPanel(metric=metric, total=total))
        return tuple(panels)


__all__ = [
    "LatencyPanel",
    "ThroughputPanel",
    "DashboardProjection",
    "DashboardProjectionBuilder",
    "DEFAULT_LATENCY_METRICS",
    "DEFAULT_THROUGHPUT_METRICS",
]
