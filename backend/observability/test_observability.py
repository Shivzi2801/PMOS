"""Unit tests for PMOS Slice S2.6 — Observability & Monitoring.

Run with:  pytest test_observability.py

The tests use injectable clocks and in-memory sinks throughout so that no
test touches wall-clock time, the network, or any external service. They
exercise the metric pipeline, telemetry sinks, tracing, the metric facades,
token/cost ledgers, usage reporting, the alert state machine, SLA attainment,
health aggregation, dashboard projection, and the composed
:class:`ObservabilityService` — including its non-fatal exception isolation.
"""

from __future__ import annotations

import pytest

from .errors import (
    DuplicateMetricError,
    LabelError,
    MetricNotRegisteredError,
    MetricTypeError,
    TraceContextError,
    SpanError,
    CostTrackingError,
)
from .metrics_registry import (
    MetricDefinition,
    MetricKind,
    MetricUnit,
    MetricsRegistry,
    build_default_registry,
)
from .metrics_collector import MetricsCollector
from .metrics_snapshot import HistogramState
from .telemetry_event import (
    EventCategory,
    EventContext,
    EventSeverity,
    TelemetryEvent,
)
from .telemetry_sink import (
    CompositeTelemetrySink,
    FilteringTelemetrySink,
    InMemoryTelemetrySink,
)
from .trace_context import TraceContext, TRACEPARENT_HEADER
from .trace_span import SpanKind, SpanStatus, TraceSpan
from .distributed_tracer import DistributedTracer
from .workflow_tracer import WorkflowTracer
from .retrieval_metrics import RetrievalMetrics, RetrievalOutcome
from .generation_metrics import GenerationMetrics, GenerationOutcome
from .grounding_metrics import GroundingMetrics, GroundingOutcome
from .ingestion_metrics import IngestionMetrics, IngestionOutcome, IngestionStage
from .api_metrics import ApiMetrics
from .token_tracker import TokenTracker
from .cost_tracker import CostTracker, PricingTable, ModelPricing
from .usage_report import UsageReportBuilder
from .alert_rule import (
    AlertRule,
    AlertSeverity,
    ComparisonOp,
    CounterRateCondition,
    GaugeThresholdCondition,
    LatencyQuantileCondition,
    HealthCondition,
)
from .alert_engine import AlertEngine, AlertState
from .sla_monitor import AvailabilitySlo, LatencySlo, SlaMonitor
from .health_status import HealthState, HealthReport
from .service_health import CallableHealthCheck, DependencyHealthCheck
from .health_aggregator import HealthAggregator
from .dashboard_projection import DashboardProjectionBuilder
from .observability_service import (
    ObservabilityService,
    build_observability_service,
)


class FakeClock:
    """Deterministic monotonic-ish clock advanced explicitly by tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def tick(self, seconds: float) -> None:
        self._t += seconds


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_registry_register_and_get():
    reg = MetricsRegistry()
    d = MetricDefinition(
        name="pmos.test.counter",
        kind=MetricKind.COUNTER,
        description="t",
        unit=MetricUnit.COUNT,
        label_keys=("tenant",),
    )
    reg.register(d)
    assert reg.contains("pmos.test.counter")
    assert reg.get("pmos.test.counter") is d
    assert reg.try_get("missing") is None


def test_registry_duplicate_raises():
    reg = MetricsRegistry()
    reg.register(MetricDefinition("m", MetricKind.GAUGE, "d", MetricUnit.COUNT))
    # Idempotent re-registration of an identical definition is allowed.
    reg.register(MetricDefinition("m", MetricKind.GAUGE, "d", MetricUnit.COUNT))
    # A conflicting redefinition under the same name must raise.
    with pytest.raises(DuplicateMetricError):
        reg.register(
            MetricDefinition("m", MetricKind.COUNTER, "d", MetricUnit.COUNT))


def test_registry_get_missing_raises():
    reg = MetricsRegistry()
    with pytest.raises(MetricNotRegisteredError):
        reg.get("nope")


def test_definition_validate_labels():
    d = MetricDefinition(
        "m", MetricKind.COUNTER, "d", MetricUnit.COUNT, label_keys=("a", "b")
    )
    d.validate_labels({"a": "1", "b": "2"})
    with pytest.raises(LabelError):
        d.validate_labels({"a": "1"})  # missing b
    with pytest.raises(LabelError):
        d.validate_labels({"a": "1", "b": "2", "c": "3"})  # unknown c


def test_default_registry_has_canonical_metrics():
    reg = build_default_registry()
    for name in (
        "pmos.api.requests_total",
        "pmos.generation.cost_usd",
        "pmos.tokens_total",
        "pmos.cost_usd_total",
    ):
        assert reg.contains(name)


# --------------------------------------------------------------------------- #
# Collector + snapshot
# --------------------------------------------------------------------------- #


def make_collector() -> MetricsCollector:
    return MetricsCollector(build_default_registry())


def test_counter_increment_and_snapshot():
    c = make_collector()
    labels = {"method": "GET", "route": "/x", "status": "200", "tenant": "t1"}
    c.increment("pmos.api.requests_total", labels=labels)
    c.increment("pmos.api.requests_total", labels=labels)
    snap = c.snapshot()
    total = snap.counter_total("pmos.api.requests_total")
    assert total == 2.0


def test_gauge_set_and_adjust():
    c = make_collector()
    c.set_gauge("pmos.api.in_flight", 5, labels={"tenant": "t1"})
    c.adjust_gauge("pmos.api.in_flight", -2, labels={"tenant": "t1"})
    sample = c.snapshot().find("pmos.api.in_flight", {"tenant": "t1"})
    assert sample is not None and sample.value == 3.0


def test_histogram_observe_quantiles():
    c = make_collector()
    for v in (10, 20, 30, 40, 50):
        c.observe("pmos.retrieval.latency_ms", v,
                  labels={"strategy": "hybrid", "tenant": "t1"})
    sample = c.snapshot().find(
        "pmos.retrieval.latency_ms", {"strategy": "hybrid", "tenant": "t1"})
    assert sample is not None
    h = sample.histogram
    assert isinstance(h, HistogramState)
    assert h.count == 5
    assert h.sum == 150
    assert h.min == 10 and h.max == 50
    assert h.mean == 30


def test_collector_unregistered_metric_raises():
    c = make_collector()
    with pytest.raises(MetricNotRegisteredError):
        c.increment("does.not.exist")


def test_collector_wrong_kind_raises():
    c = make_collector()
    # requests_total is a counter; observing it as histogram is a type error
    with pytest.raises(MetricTypeError):
        c.observe("pmos.api.requests_total", 5,
                  labels={"method": "GET", "route": "/x", "status": "200",
                          "tenant": "t1"})


def test_histogram_quantile_interpolation():
    h = HistogramState(
        bucket_bounds=(10.0, 20.0, 30.0, float("inf")),
        bucket_counts=(1, 1, 1, 0),
        count=3,
        sum=45.0,
        min=5.0,
        max=25.0,
    )
    # median should fall within observed range
    q = h.quantile(0.5)
    assert h.min <= q <= h.max


# --------------------------------------------------------------------------- #
# Telemetry sinks
# --------------------------------------------------------------------------- #


def make_event(sev=EventSeverity.INFO, cat=EventCategory.SYSTEM, name="e"):
    return TelemetryEvent(name=name, category=cat, severity=sev)


def test_in_memory_sink_query_and_capacity():
    sink = InMemoryTelemetrySink(capacity=3)
    for i in range(5):
        sink.emit(make_event(name=f"e{i}"))
    assert sink.total_emitted == 5
    assert sink.total_dropped == 2
    assert len(sink.events()) == 3  # ring buffer keeps last 3


def test_filtering_sink_severity_floor():
    inner = InMemoryTelemetrySink()
    sink = FilteringTelemetrySink(inner, min_severity=EventSeverity.WARNING)
    sink.emit(make_event(sev=EventSeverity.INFO))
    sink.emit(make_event(sev=EventSeverity.ERROR))
    assert inner.total_emitted == 1


def test_composite_sink_isolates_failures():
    class Boom(InMemoryTelemetrySink):
        def emit(self, event):  # type: ignore[override]
            raise RuntimeError("sink down")

    good = InMemoryTelemetrySink()
    errors_seen = []
    comp = CompositeTelemetrySink(
        [Boom(), good],
        on_error=lambda exc: errors_seen.append(exc))
    comp.emit(make_event())
    assert good.total_emitted == 1  # healthy sink still received it
    assert len(errors_seen) == 1


# --------------------------------------------------------------------------- #
# Trace context + spans
# --------------------------------------------------------------------------- #


def test_trace_context_header_roundtrip():
    ctx = TraceContext.new_root(sampled=True)
    headers = ctx.to_headers()
    assert TRACEPARENT_HEADER in headers
    parsed = TraceContext.from_headers(headers)
    assert parsed is not None
    assert parsed.trace_id == ctx.trace_id
    assert parsed.span_id == ctx.span_id
    assert parsed.sampled is True


def test_trace_context_absent_returns_none():
    assert TraceContext.from_headers({}) is None


def test_trace_context_malformed_raises():
    with pytest.raises(TraceContextError):
        TraceContext.from_headers({TRACEPARENT_HEADER: "garbage"})


def test_trace_context_child_keeps_trace_id():
    root = TraceContext.new_root()
    child = root.child()
    assert child.trace_id == root.trace_id
    assert child.span_id != root.span_id


def test_span_lifecycle_and_double_end():
    ctx = TraceContext.new_root()
    clock = FakeClock()
    span = TraceSpan(name="op", context=ctx, parent_span_id=None,
                     kind=SpanKind.INTERNAL, clock=clock)
    span.set_attribute("k", "v")
    clock.tick(0.05)
    data = span.end()
    assert data.duration_ms == pytest.approx(50.0)
    assert data.attributes["k"] == "v"
    with pytest.raises(SpanError):
        span.end()


def test_span_records_exception_status():
    ctx = TraceContext.new_root()
    captured = {}
    span = TraceSpan(name="op", context=ctx, parent_span_id=None,
                     kind=SpanKind.INTERNAL, clock=FakeClock(),
                     on_end=lambda d: captured.update(status=d.status))
    try:
        with span:
            raise ValueError("kaboom")
    except ValueError:
        pass
    assert captured.get("status") == SpanStatus.ERROR


# --------------------------------------------------------------------------- #
# Distributed + workflow tracer
# --------------------------------------------------------------------------- #


def test_distributed_tracer_context_propagation():
    sink = InMemoryTelemetrySink()
    tracer = DistributedTracer(
        service_name="svc", event_emitter=sink.emit, clock=FakeClock())
    assert tracer.current_context() is None
    with tracer.span("outer") as outer:
        assert tracer.current_context() is not None
        with tracer.span("inner") as inner:
            assert inner.context.trace_id == outer.context.trace_id
    # both spans emitted trace events
    assert sink.total_emitted == 2


def test_tracer_inject_extract_roundtrip():
    tracer = DistributedTracer(service_name="svc", clock=FakeClock())
    with tracer.span("op"):
        headers = tracer.inject()
        assert TRACEPARENT_HEADER in headers
        extracted = tracer.extract(headers)
        assert extracted is not None


def test_workflow_tracer_records_metrics():
    c = make_collector()
    sink = InMemoryTelemetrySink()
    tracer = DistributedTracer(
        service_name="svc", event_emitter=sink.emit, clock=FakeClock())
    recorded = []
    wf = WorkflowTracer(
        tracer,
        record_workflow=lambda *a, **k: recorded.append(("wf", a, k)),
        record_step=lambda *a, **k: recorded.append(("step", a, k)),
        clock=FakeClock(),
    )
    with wf.workflow_span("ingest", tenant_id="t1"):
        with wf.step_span("ingest", "extract"):
            pass
    assert any(r[0] == "wf" for r in recorded)
    assert any(r[0] == "step" for r in recorded)


# --------------------------------------------------------------------------- #
# Metric facades
# --------------------------------------------------------------------------- #


def test_retrieval_metrics_empty_increment():
    c = make_collector()
    rm = RetrievalMetrics(c)
    rm.record(RetrievalOutcome(strategy="hybrid", tenant_id="t1",
                               duration_ms=12.0, result_count=0))
    snap = c.snapshot()
    assert snap.counter_total("pmos.retrieval.empty_total") == 1.0


def test_generation_metrics_rollups():
    c = make_collector()
    gm = GenerationMetrics(c)
    gm.record(GenerationOutcome(
        model="claude-sonnet-4", tenant_id="t1", duration_ms=900.0,
        prompt_tokens=100, completion_tokens=50, cost_usd=0.01, success=True))
    snap = c.snapshot()
    assert snap.counter_total("pmos.tokens_total") == 150.0
    assert snap.counter_total("pmos.cost_usd_total") == pytest.approx(0.01)


def test_grounding_metrics_record():
    c = make_collector()
    GroundingMetrics(c).record(GroundingOutcome(
        tenant_id="t1", duration_ms=30.0, score=0.92, unsupported_claims=1))
    snap = c.snapshot()
    sample = snap.find("pmos.grounding.unsupported_claims", {"tenant": "t1"})
    assert sample is not None and sample.histogram is not None
    assert sample.histogram.sum == 1.0


def test_ingestion_metrics_record():
    c = make_collector()
    IngestionMetrics(c).record(IngestionOutcome(
        stage=IngestionStage.INDEX, connector="gdrive", tenant_id="t1",
        duration_ms=120.0, success=True, chunks=10))
    snap = c.snapshot()
    assert snap.counter_total("pmos.ingestion.chunks_total") == 10.0


def test_api_metrics_track_request_context():
    c = make_collector()
    api = ApiMetrics(c, clock=FakeClock())
    with api.track_request(method="GET", route="/x", tenant_id="t1") as tr:
        tr.set_status(200)
    snap = c.snapshot()
    assert snap.counter_total("pmos.api.requests_total") == 1.0
    inflight = snap.find("pmos.api.in_flight", {"tenant": "t1"})
    assert inflight is None or inflight.value == 0.0


def test_api_metrics_exception_marks_500():
    c = make_collector()
    api = ApiMetrics(c, clock=FakeClock())
    with pytest.raises(RuntimeError):
        with api.track_request(method="POST", route="/y", tenant_id="t1"):
            raise RuntimeError("boom")
    snap = c.snapshot()
    errs = snap.counter_total("pmos.api.errors_total")
    assert errs == 1.0


# --------------------------------------------------------------------------- #
# Token + cost + usage
# --------------------------------------------------------------------------- #


def test_token_tracker_ledger():
    t = TokenTracker()
    t.record(tenant_id="t1", model="m", prompt_tokens=10, completion_tokens=5)
    t.record(tenant_id="t1", model="m", prompt_tokens=20, completion_tokens=5)
    assert t.tenant_total("t1") == 40
    assert t.total_tokens() == 40


def test_cost_tracker_known_and_unknown_model():
    ct = CostTracker(PricingTable.with_defaults())
    cost = ct.record(tenant_id="t1", model="claude-sonnet-4",
                     prompt_tokens=1000, completion_tokens=1000)
    assert cost > 0
    strict = CostTracker(PricingTable())  # no defaults, unknown model
    with pytest.raises(CostTrackingError):
        strict.estimate(model="mystery", prompt_tokens=1, completion_tokens=1)


def test_usage_report_merges_tokens_and_cost():
    tt = TokenTracker()
    ct = CostTracker(PricingTable.with_defaults())
    tt.record(tenant_id="t1", model="claude-sonnet-4",
              prompt_tokens=1000, completion_tokens=500)
    ct.record(tenant_id="t1", model="claude-sonnet-4",
              prompt_tokens=1000, completion_tokens=500)
    report = UsageReportBuilder(tt, ct, clock=FakeClock()).build(
        period_label="june")
    assert report.total_tokens == 1500
    assert report.total_cost_usd > 0
    summary = report.tenant("t1")
    assert summary is not None
    assert summary.total_tokens == 1500


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #


def test_alert_engine_state_machine_with_debounce():
    c = make_collector()
    clock = FakeClock()
    engine = AlertEngine(clock=clock)
    rule = AlertRule(
        name="high-inflight",
        condition=GaugeThresholdCondition(
            metric="pmos.api.in_flight", op=ComparisonOp.GT, threshold=10),
        severity=AlertSeverity.WARNING,
        for_consecutive=2,
    )
    engine.register(rule)

    # breach once -> PENDING (debounce not satisfied)
    c.set_gauge("pmos.api.in_flight", 20, labels={"tenant": "t1"})
    engine.evaluate(c.snapshot(), None)
    alert = engine.active_alerts()
    # not yet firing because for_consecutive=2
    assert all(a.state != AlertState.FIRING for a in alert) or len(alert) == 0

    # breach again -> FIRING
    engine.evaluate(c.snapshot(), None)
    active = engine.active_alerts()
    assert any(a.state == AlertState.FIRING for a in active)

    # clear breach -> RESOLVED
    c.set_gauge("pmos.api.in_flight", 0, labels={"tenant": "t1"})
    transitions = engine.evaluate(c.snapshot(), None)
    assert any(t.state == AlertState.RESOLVED for t in transitions)


def test_alert_engine_isolates_bad_rule():
    class BadCondition(GaugeThresholdCondition):
        def evaluate(self, snapshot, health=None):  # type: ignore[override]
            raise RuntimeError("rule blew up")

    c = make_collector()
    engine = AlertEngine(clock=FakeClock())
    engine.register(AlertRule(
        name="bad",
        condition=BadCondition(
            metric="pmos.api.in_flight", op=ComparisonOp.GT, threshold=1),
    ))
    # should not raise despite the broken condition
    engine.evaluate(c.snapshot(), None)


# --------------------------------------------------------------------------- #
# SLA monitor
# --------------------------------------------------------------------------- #


def test_sla_availability_attainment():
    c = make_collector()
    for _ in range(98):
        c.increment("pmos.api.requests_total",
                    labels={"method": "GET", "route": "/x", "status": "200",
                            "tenant": "t1"})
    for _ in range(2):
        c.increment("pmos.api.errors_total",
                    labels={"route": "/x", "error_type": "ServerError",
                            "tenant": "t1"})
    mon = SlaMonitor()
    mon.add_availability(AvailabilitySlo(
        name="api-availability",
        total_metric="pmos.api.requests_total",
        error_metric="pmos.api.errors_total",
        target=0.95,
    ))
    statuses = mon.evaluate(c.snapshot())
    assert len(statuses) == 1
    # 2 errors / 98 requests -> ~97.96% attainment, above 95% target
    assert statuses[0].to_dict()["attainment"] >= 0.95


def test_sla_generates_alert_rules():
    mon = SlaMonitor()
    mon.add_latency(LatencySlo(
        name="api-latency", latency_metric="pmos.api.request_latency_ms",
        quantile=0.95, budget_ms=500))
    rules = mon.alert_rules()
    assert len(rules) == 1
    assert isinstance(rules[0], AlertRule)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


def test_health_aggregator_all_healthy():
    agg = HealthAggregator(clock=FakeClock())
    agg.register(CallableHealthCheck("db", lambda: HealthState.HEALTHY))
    agg.register(CallableHealthCheck("cache", lambda: True))
    report = agg.evaluate()
    assert report.state == HealthState.HEALTHY
    assert report.is_ready


def test_health_critical_unhealthy_drives_overall():
    agg = HealthAggregator(clock=FakeClock())
    agg.register(CallableHealthCheck("db", lambda: HealthState.UNHEALTHY,
                                     critical=True))
    report = agg.evaluate()
    assert report.state == HealthState.UNHEALTHY
    assert not report.is_ready


def test_health_noncritical_caps_at_degraded():
    agg = HealthAggregator(clock=FakeClock())
    agg.register(CallableHealthCheck("db", lambda: HealthState.HEALTHY))
    agg.register(CallableHealthCheck(
        "optional", lambda: HealthState.UNHEALTHY, critical=False))
    report = agg.evaluate()
    assert report.state == HealthState.DEGRADED


def test_health_check_exception_is_unknown():
    agg = HealthAggregator(clock=FakeClock())

    def boom():
        raise RuntimeError("probe failed")

    agg.register(CallableHealthCheck("flaky", boom, critical=False))
    report = agg.evaluate()
    comp = report.component("flaky")
    assert comp is not None
    assert comp.state in (HealthState.UNKNOWN, HealthState.UNHEALTHY)


def test_dependency_health_check_success():
    chk = DependencyHealthCheck(
        "remote", ping=lambda: None, clock=FakeClock())
    result = chk.check()
    assert result.state == HealthState.HEALTHY


# --------------------------------------------------------------------------- #
# Dashboard projection
# --------------------------------------------------------------------------- #


def test_dashboard_projection_builds():
    c = make_collector()
    c.observe("pmos.api.request_latency_ms", 42.0,
              labels={"method": "GET", "route": "/x", "tenant": "t1"})
    builder = DashboardProjectionBuilder(clock=FakeClock())
    proj = builder.build(c.snapshot(), health=None, alerts=(), slos=())
    d = proj.to_dict()
    assert isinstance(d, dict)
    assert "latency" in d and "throughput" in d


# --------------------------------------------------------------------------- #
# ObservabilityService integration
# --------------------------------------------------------------------------- #


def test_service_record_generation_returns_cost():
    svc = build_observability_service(service_name="pmos", clock=FakeClock())
    cost = svc.record_generation(
        model="claude-sonnet-4", tenant_id="t1", duration_ms=800.0,
        prompt_tokens=1000, completion_tokens=500)
    assert cost > 0
    report = svc.usage_report()
    assert report.total_tokens == 1500


def test_service_end_to_end_flow():
    svc = build_observability_service(service_name="pmos", clock=FakeClock())
    svc.record_retrieval(RetrievalOutcome(
        strategy="hybrid", tenant_id="t1", duration_ms=20.0, result_count=5,
        top_score=0.8))
    svc.record_grounding(GroundingOutcome(
        tenant_id="t1", duration_ms=15.0, score=0.9))
    svc.register_health_check(
        CallableHealthCheck("db", lambda: HealthState.HEALTHY))
    report = svc.evaluate_health()
    assert report.is_ready
    snap = svc.snapshot()
    assert snap.counter_total("pmos.retrieval.empty_total") == 0.0
    dash = svc.dashboard()
    assert dash is not None


def test_service_never_crashes_on_bad_input():
    svc = build_observability_service(service_name="pmos", clock=FakeClock())
    # unknown model must not raise from the service boundary
    cost = svc.record_generation(
        model="totally-unknown-model", tenant_id="t1", duration_ms=10.0,
        prompt_tokens=5, completion_tokens=5)
    assert cost == 0.0  # cost silently falls back to 0


def test_service_emits_events_to_in_memory_sink():
    svc = build_observability_service(service_name="pmos", clock=FakeClock())
    svc.event("custom.event", category=EventCategory.SYSTEM,
              severity=EventSeverity.INFO, foo="bar")
    assert svc.in_memory_sink.total_emitted >= 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
