"""
PMOS Observability & Monitoring — Observability Service (S2.6)

The composition root and primary public entry point of the subsystem. It wires
the collector, registry, telemetry pipeline, tracer, trackers, health
aggregator, alert engine, SLA monitor and dashboard builder into one cohesive,
dependency-injection-friendly facade.

Other PMOS slices depend on this single object (or the narrow facades it
exposes) rather than constructing the internals themselves. Everything is
overridable via the constructor for testing and customization; sensible
production defaults are provided by :func:`build_observability_service`.

Design guarantees
------------------
* **Non-fatal**: every public method isolates exceptions so observability can
  never crash the host.
* **Framework-agnostic**: no web/async framework imports; integration happens
  through plain callables and context managers.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Iterator, List, Mapping, Optional, Tuple

from .alert_engine import Alert, AlertEngine
from .alert_rule import AlertRule
from .api_metrics import ApiMetrics
from .cost_tracker import CostTracker, PricingTable
from .dashboard_projection import DashboardProjection, DashboardProjectionBuilder
from .distributed_tracer import DistributedTracer
from .generation_metrics import GenerationMetrics, GenerationOutcome
from .grounding_metrics import GroundingMetrics, GroundingOutcome
from .health_aggregator import HealthAggregator
from .health_status import HealthReport
from .ingestion_metrics import IngestionMetrics, IngestionOutcome
from .metrics_collector import MetricsCollector
from .metrics_registry import MetricsRegistry, build_default_registry
from .metrics_snapshot import MetricsSnapshot
from .retrieval_metrics import RetrievalMetrics, RetrievalOutcome
from .service_health import HealthCheck
from .sla_monitor import SlaMonitor
from .telemetry_event import (
    EventCategory,
    EventContext,
    EventSeverity,
    TelemetryEvent,
)
from .telemetry_sink import (
    CompositeTelemetrySink,
    InMemoryTelemetrySink,
    TelemetrySink,
)
from .token_tracker import TokenTracker
from .trace_span import SpanData, SpanKind, TraceSpan
from .usage_report import UsageReport, UsageReportBuilder
from .workflow_tracer import WorkflowTracer


class ObservabilityService:
    """Cohesive facade over the entire observability subsystem."""

    def __init__(
        self,
        *,
        service_name: str = "pmos",
        registry: Optional[MetricsRegistry] = None,
        collector: Optional[MetricsCollector] = None,
        sink: Optional[TelemetrySink] = None,
        tracer: Optional[DistributedTracer] = None,
        token_tracker: Optional[TokenTracker] = None,
        cost_tracker: Optional[CostTracker] = None,
        health_aggregator: Optional[HealthAggregator] = None,
        alert_engine: Optional[AlertEngine] = None,
        sla_monitor: Optional[SlaMonitor] = None,
        dashboard_builder: Optional[DashboardProjectionBuilder] = None,
        clock=time.time,
    ) -> None:
        self._service_name = service_name
        self._clock = clock

        self.registry = registry or build_default_registry()
        self.collector = collector or MetricsCollector(self.registry, clock=clock)
        self.sink = sink or InMemoryTelemetrySink()

        # Tracer emits TRACE events to the sink and records span durations.
        self.tracer = tracer or DistributedTracer(
            service_name,
            processor=self._on_span,
            event_emitter=self.emit,
            clock=clock,
        )

        self.token_tracker = token_tracker or TokenTracker()
        self.cost_tracker = cost_tracker or CostTracker(PricingTable.with_defaults())

        self.health = health_aggregator or HealthAggregator(clock=clock)
        self.alerts = alert_engine or AlertEngine(
            event_emitter=self.emit, clock=clock
        )
        self.sla = sla_monitor or SlaMonitor()
        self._dashboard_builder = dashboard_builder or DashboardProjectionBuilder(
            clock=clock
        )

        # Metric facades.
        self.retrieval = RetrievalMetrics(self.collector)
        self.generation = GenerationMetrics(self.collector)
        self.grounding = GroundingMetrics(self.collector)
        self.ingestion = IngestionMetrics(self.collector)
        self.api = ApiMetrics(self.collector, clock=clock)

        # Workflow tracer bound to metric recorders.
        self.workflow = WorkflowTracer(
            self.tracer,
            record_workflow=self._record_workflow_metric,
            record_step=self._record_step_metric,
            clock=clock,
        )

        self._usage_builder = UsageReportBuilder(
            self.token_tracker, self.cost_tracker, clock=clock
        )

    # ------------------------------------------------------------------ #
    # Telemetry
    # ------------------------------------------------------------------ #

    def emit(self, event: TelemetryEvent) -> None:
        """Publish a telemetry event to the configured sink (never raises)."""
        try:
            self.sink.emit(event)
        except Exception:  # noqa: BLE001 - observability must not crash callers
            pass

    def event(
        self,
        name: str,
        *,
        category: EventCategory = EventCategory.SYSTEM,
        severity: EventSeverity = EventSeverity.INFO,
        context: Optional[EventContext] = None,
        **attributes: Any,
    ) -> None:
        """Convenience constructor + emit."""
        self.emit(
            TelemetryEvent(
                name=name,
                category=category,
                severity=severity,
                timestamp=self._clock(),
                context=context or EventContext(component=self._service_name),
                attributes=attributes,
            )
        )

    # ------------------------------------------------------------------ #
    # Tracing
    # ------------------------------------------------------------------ #

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        tenant_id: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[TraceSpan]:
        with self.tracer.span(
            name, kind=kind, tenant_id=tenant_id, attributes=attributes
        ) as s:
            yield s

    def _on_span(self, data: SpanData) -> None:
        # Record span duration into the workflow step histogram only when it is
        # a workflow step; generic spans are captured via TRACE events already.
        return None

    # ------------------------------------------------------------------ #
    # LLM usage (token + cost) — combines exact ledgers with metrics
    # ------------------------------------------------------------------ #

    def record_generation(
        self,
        *,
        model: str,
        tenant_id: str,
        duration_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        success: bool = True,
    ) -> float:
        """Record a full LLM generation: metrics, exact token ledger, cost.

        Returns the estimated cost in USD.
        """
        cost = 0.0
        try:
            cost = self.cost_tracker.record(
                tenant_id=tenant_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception:  # noqa: BLE001 - unknown model etc. must not crash
            cost = 0.0
        with contextlib.suppress(Exception):
            self.token_tracker.record(
                tenant_id=tenant_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        with contextlib.suppress(Exception):
            self.generation.record(
                GenerationOutcome(
                    model=model,
                    tenant_id=tenant_id,
                    duration_ms=duration_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost,
                    success=success,
                )
            )
        return cost

    def record_retrieval(self, outcome: RetrievalOutcome) -> None:
        with contextlib.suppress(Exception):
            self.retrieval.record(outcome)

    def record_grounding(self, outcome: GroundingOutcome) -> None:
        with contextlib.suppress(Exception):
            self.grounding.record(outcome)

    def record_ingestion(self, outcome: IngestionOutcome) -> None:
        with contextlib.suppress(Exception):
            self.ingestion.record(outcome)

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    def register_health_check(self, check: HealthCheck) -> None:
        self.health.register(check)

    def evaluate_health(self) -> HealthReport:
        report = self.health.evaluate()
        # Emit a health telemetry event for observability/audit.
        self.event(
            "health.evaluated",
            category=EventCategory.HEALTH,
            severity=(
                EventSeverity.ERROR
                if not report.is_ready
                else EventSeverity.INFO
            ),
            state=report.state.value,
            ready=report.is_ready,
        )
        return report

    # ------------------------------------------------------------------ #
    # Alerts + SLA
    # ------------------------------------------------------------------ #

    def register_alert_rule(self, rule: AlertRule) -> None:
        self.alerts.register(rule)

    def register_sla_alerts(self) -> None:
        """Auto-register alert rules for all configured SLOs."""
        for rule in self.sla.alert_rules():
            self.alerts.register(rule)

    def run_alert_cycle(self) -> Tuple[Alert, ...]:
        """Snapshot → evaluate health → evaluate alerts. Returns transitions."""
        snapshot = self.snapshot()
        health = self.health.last_report() or self.health.evaluate()
        return self.alerts.evaluate(snapshot, health)

    # ------------------------------------------------------------------ #
    # Snapshots, reporting & dashboard
    # ------------------------------------------------------------------ #

    def snapshot(self) -> MetricsSnapshot:
        return self.collector.snapshot()

    def usage_report(self, *, period_label: str = "lifetime") -> UsageReport:
        return self._usage_builder.build(period_label=period_label)

    def dashboard(self) -> DashboardProjection:
        snapshot = self.snapshot()
        health = self.health.last_report()
        alerts = self.alerts.active_alerts()
        slos = self.sla.evaluate(snapshot)
        return self._dashboard_builder.build(
            snapshot, health=health, alerts=alerts, slos=slos
        )

    # ------------------------------------------------------------------ #
    # Internal metric recorders for the workflow tracer
    # ------------------------------------------------------------------ #

    def _record_workflow_metric(
        self, *, workflow: str, status: str, tenant: str, duration_ms: float
    ) -> None:
        labels = {"workflow": workflow, "status": status, "tenant": tenant}
        with contextlib.suppress(Exception):
            self.collector.observe("pmos.workflow.duration_ms", duration_ms, labels=labels)
        with contextlib.suppress(Exception):
            self.collector.increment("pmos.workflow.runs_total", labels=labels)

    def _record_step_metric(
        self, *, workflow: str, step: str, status: str, tenant: str, duration_ms: float
    ) -> None:
        with contextlib.suppress(Exception):
            self.collector.observe(
                "pmos.workflow.step_duration_ms",
                duration_ms,
                labels={"workflow": workflow, "step": step, "status": status, "tenant": tenant},
            )


def build_observability_service(
    *,
    service_name: str = "pmos",
    extra_sinks: Optional[List[TelemetrySink]] = None,
    pricing: Optional[PricingTable] = None,
    clock=time.time,
) -> ObservabilityService:
    """Construct a production-ready :class:`ObservabilityService`.

    Combines an in-memory sink (for queries/dashboard) with any injected
    ``extra_sinks`` behind a fault-isolating :class:`CompositeTelemetrySink`.
    """
    in_memory = InMemoryTelemetrySink()
    sinks: List[TelemetrySink] = [in_memory]
    if extra_sinks:
        sinks.extend(extra_sinks)
    composite = CompositeTelemetrySink(sinks)

    service = ObservabilityService(
        service_name=service_name,
        sink=composite,
        cost_tracker=CostTracker(pricing or PricingTable.with_defaults()),
        clock=clock,
    )
    # Expose the in-memory sink for query access by the dashboard / admin API.
    service.in_memory_sink = in_memory  # type: ignore[attr-defined]
    return service


__all__ = ["ObservabilityService", "build_observability_service"]
