"""PMOS Slice S2.6 — Observability & Monitoring.

The observability subsystem gives PMOS visibility into platform health,
workflow execution, retrieval/generation/grounding quality, latency, cost,
token usage, errors and operational state. It is framework-agnostic,
dependency-injection friendly, strongly typed, and depends on no external
services.

The primary entry point is :class:`ObservabilityService` (or the
:func:`build_observability_service` factory), which composes the metric
collector, telemetry sinks, distributed tracer, health aggregator, alert
engine, SLA monitor, token/cost ledgers and dashboard projection behind a
single, exception-isolated facade. Every other PMOS slice depends only on
that facade.
"""

from __future__ import annotations

__version__ = "2.6.0"

# Errors ------------------------------------------------------------------- #
from .errors import (
    ObservabilityError,
    MetricError,
    MetricNotRegisteredError,
    DuplicateMetricError,
    MetricTypeError,
    LabelError,
    TelemetryError,
    SinkError,
    TracingError,
    SpanError,
    TraceContextError,
    AlertError,
    InvalidAlertRuleError,
    HealthError,
    UnknownComponentError,
    ReportingError,
    CostTrackingError,
)

# Metrics ------------------------------------------------------------------ #
from .metrics_registry import (
    MetricKind,
    MetricUnit,
    MetricDefinition,
    MetricsRegistry,
    build_default_registry,
    DEFAULT_LATENCY_BUCKETS_MS,
    DEFAULT_TOKEN_BUCKETS,
)
from .metrics_snapshot import (
    HistogramState,
    MetricSample,
    MetricsSnapshot,
    normalize_labels,
    labels_to_dict,
)
from .metrics_collector import MetricsCollector

# Telemetry ---------------------------------------------------------------- #
from .telemetry_event import (
    EventSeverity,
    EventCategory,
    EventContext,
    TelemetryEvent,
)
from .telemetry_sink import (
    TelemetrySink,
    InMemoryTelemetrySink,
    LoggingTelemetrySink,
    FilteringTelemetrySink,
    CompositeTelemetrySink,
)

# Tracing ------------------------------------------------------------------ #
from .trace_context import (
    TraceContext,
    generate_trace_id,
    generate_span_id,
    TRACEPARENT_HEADER,
    TRACESTATE_HEADER,
)
from .trace_span import (
    SpanKind,
    SpanStatus,
    SpanEvent,
    SpanData,
    TraceSpan,
    new_child_context,
)
from .distributed_tracer import DistributedTracer
from .workflow_tracer import WorkflowTracer

# Metric facades ----------------------------------------------------------- #
from .retrieval_metrics import RetrievalMetrics, RetrievalOutcome
from .generation_metrics import GenerationMetrics, GenerationOutcome
from .grounding_metrics import GroundingMetrics, GroundingOutcome
from .ingestion_metrics import IngestionMetrics, IngestionOutcome, IngestionStage
from .api_metrics import ApiMetrics, RequestTracker

# LLM cost / tokens / usage ------------------------------------------------ #
from .token_tracker import TokenKind, TokenLedgerEntry, TokenTracker
from .cost_tracker import (
    ModelPricing,
    PricingTable,
    CostLedgerEntry,
    CostTracker,
)
from .usage_report import (
    ModelUsage,
    TenantUsageSummary,
    UsageReport,
    UsageReportBuilder,
)

# Alerts ------------------------------------------------------------------- #
from .alert_rule import (
    AlertSeverity,
    ComparisonOp,
    ConditionResult,
    AlertCondition,
    CounterRateCondition,
    LatencyQuantileCondition,
    GaugeThresholdCondition,
    HealthCondition,
    AlertRule,
)
from .alert_engine import AlertState, Alert, AlertEngine

# Health ------------------------------------------------------------------- #
from .health_status import HealthState, HealthCheckResult, HealthReport
from .service_health import (
    HealthCheck,
    CallableHealthCheck,
    DependencyHealthCheck,
    MetricThresholdHealthCheck,
)
from .health_aggregator import HealthAggregator

# SLA / dashboard ---------------------------------------------------------- #
from .sla_monitor import (
    SloKind,
    AvailabilitySlo,
    LatencySlo,
    SloStatus,
    SlaMonitor,
)
from .dashboard_projection import (
    LatencyPanel,
    ThroughputPanel,
    DashboardProjection,
    DashboardProjectionBuilder,
)

# Composition root --------------------------------------------------------- #
from .observability_service import (
    ObservabilityService,
    build_observability_service,
)

__all__ = [
    "__version__",
    # errors
    "ObservabilityError", "MetricError", "MetricNotRegisteredError",
    "DuplicateMetricError", "MetricTypeError", "LabelError", "TelemetryError",
    "SinkError", "TracingError", "SpanError", "TraceContextError",
    "AlertError", "InvalidAlertRuleError", "HealthError",
    "UnknownComponentError", "ReportingError", "CostTrackingError",
    # metrics
    "MetricKind", "MetricUnit", "MetricDefinition", "MetricsRegistry",
    "build_default_registry", "DEFAULT_LATENCY_BUCKETS_MS",
    "DEFAULT_TOKEN_BUCKETS", "HistogramState", "MetricSample",
    "MetricsSnapshot", "normalize_labels", "labels_to_dict",
    "MetricsCollector",
    # telemetry
    "EventSeverity", "EventCategory", "EventContext", "TelemetryEvent",
    "TelemetrySink", "InMemoryTelemetrySink", "LoggingTelemetrySink",
    "FilteringTelemetrySink", "CompositeTelemetrySink",
    # tracing
    "TraceContext", "generate_trace_id", "generate_span_id",
    "TRACEPARENT_HEADER", "TRACESTATE_HEADER", "SpanKind", "SpanStatus",
    "SpanEvent", "SpanData", "TraceSpan", "new_child_context",
    "DistributedTracer", "WorkflowTracer",
    # facades
    "RetrievalMetrics", "RetrievalOutcome", "GenerationMetrics",
    "GenerationOutcome", "GroundingMetrics", "GroundingOutcome",
    "IngestionMetrics", "IngestionOutcome", "IngestionStage", "ApiMetrics",
    "RequestTracker",
    # llm usage
    "TokenKind", "TokenLedgerEntry", "TokenTracker", "ModelPricing",
    "PricingTable", "CostLedgerEntry", "CostTracker", "ModelUsage",
    "TenantUsageSummary", "UsageReport", "UsageReportBuilder",
    # alerts
    "AlertSeverity", "ComparisonOp", "ConditionResult", "AlertCondition",
    "CounterRateCondition", "LatencyQuantileCondition",
    "GaugeThresholdCondition", "HealthCondition", "AlertRule", "AlertState",
    "Alert", "AlertEngine",
    # health
    "HealthState", "HealthCheckResult", "HealthReport", "HealthCheck",
    "CallableHealthCheck", "DependencyHealthCheck",
    "MetricThresholdHealthCheck", "HealthAggregator",
    # sla / dashboard
    "SloKind", "AvailabilitySlo", "LatencySlo", "SloStatus", "SlaMonitor",
    "LatencyPanel", "ThroughputPanel", "DashboardProjection",
    "DashboardProjectionBuilder",
    # service
    "ObservabilityService", "build_observability_service",
]
