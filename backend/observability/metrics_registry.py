"""
PMOS Observability & Monitoring — Metrics Registry (S2.6)

Defines the *vocabulary* of metrics the platform can emit. A metric must be
declared in the registry before it can be recorded; this guards against typos,
enforces a stable label schema, and lets dashboards/alerts reason about the
full catalog of available signals.

The registry is intentionally framework-agnostic: it has no dependency on
Prometheus, OpenTelemetry, StatsD, etc. Exporters (sinks) translate the
in-memory representation to whatever wire format is required.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, Mapping, Optional, Tuple

from .errors import DuplicateMetricError, LabelError, MetricNotRegisteredError


class MetricKind(str, Enum):
    """The semantic class of a metric.

    COUNTER     — monotonically increasing total (requests, errors).
    GAUGE       — point-in-time value that can rise and fall (queue depth).
    HISTOGRAM   — distribution of observed values (latency, token counts).
    """

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class MetricUnit(str, Enum):
    """Canonical units. Used by reporting/exporters for correct labeling."""

    NONE = "none"
    MILLISECONDS = "ms"
    SECONDS = "s"
    BYTES = "bytes"
    TOKENS = "tokens"
    REQUESTS = "requests"
    USD = "usd"
    RATIO = "ratio"
    COUNT = "count"


# Default histogram buckets tuned for latency in milliseconds. Buckets are
# upper-bound inclusive; the implicit +Inf bucket captures the tail.
DEFAULT_LATENCY_BUCKETS_MS: Tuple[float, ...] = (
    1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000,
)

# Buckets tuned for token counts.
DEFAULT_TOKEN_BUCKETS: Tuple[float, ...] = (
    16, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768,
)


@dataclass(frozen=True)
class MetricDefinition:
    """An immutable description of a single metric.

    Attributes
    ----------
    name:
        Dotted metric name, e.g. ``pmos.retrieval.latency_ms``.
    kind:
        One of :class:`MetricKind`.
    description:
        Human-readable explanation for dashboards & docs.
    unit:
        Canonical :class:`MetricUnit`.
    label_keys:
        The exact set of label keys every sample must carry. Order is not
        significant but the set is enforced.
    buckets:
        Histogram bucket upper bounds. Ignored for non-histogram metrics.
    """

    name: str
    kind: MetricKind
    description: str
    unit: MetricUnit = MetricUnit.NONE
    label_keys: Tuple[str, ...] = field(default_factory=tuple)
    buckets: Tuple[float, ...] = field(default_factory=tuple)

    def validate_labels(self, labels: Mapping[str, str]) -> None:
        """Raise :class:`LabelError` if ``labels`` do not match the schema."""
        provided = set(labels.keys())
        expected = set(self.label_keys)
        if provided != expected:
            missing = expected - provided
            extra = provided - expected
            raise LabelError(
                f"Label mismatch for metric '{self.name}'",
                details={
                    "metric": self.name,
                    "missing": sorted(missing),
                    "unexpected": sorted(extra),
                    "expected": sorted(expected),
                },
            )


class MetricsRegistry:
    """A thread-safe catalog of :class:`MetricDefinition` objects.

    The registry is the single source of truth for which metrics exist. The
    :class:`~pmos.observability.metrics_collector.MetricsCollector` consults it
    on every record call.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._defs: Dict[str, MetricDefinition] = {}

    def register(self, definition: MetricDefinition) -> MetricDefinition:
        """Register a metric definition.

        Idempotent for identical re-registration; raises
        :class:`DuplicateMetricError` on a conflicting redefinition.
        """
        with self._lock:
            existing = self._defs.get(definition.name)
            if existing is not None:
                if existing == definition:
                    return existing
                raise DuplicateMetricError(definition.name)
            self._defs[definition.name] = definition
            return definition

    def register_many(self, definitions: Iterable[MetricDefinition]) -> None:
        for d in definitions:
            self.register(d)

    def get(self, name: str) -> MetricDefinition:
        with self._lock:
            try:
                return self._defs[name]
            except KeyError:
                raise MetricNotRegisteredError(name) from None

    def try_get(self, name: str) -> Optional[MetricDefinition]:
        with self._lock:
            return self._defs.get(name)

    def contains(self, name: str) -> bool:
        with self._lock:
            return name in self._defs

    def all(self) -> Tuple[MetricDefinition, ...]:
        with self._lock:
            return tuple(self._defs.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._defs)


def build_default_registry() -> MetricsRegistry:
    """Construct a registry pre-populated with the canonical PMOS metrics.

    These names are referenced throughout the platform-specific metric
    facades (retrieval_metrics, generation_metrics, etc.) and form the stable
    public contract that dashboards and alerts are built against.
    """
    registry = MetricsRegistry()
    registry.register_many(
        [
            # ---- API layer (S2.1) ----
            MetricDefinition(
                "pmos.api.requests_total", MetricKind.COUNTER,
                "Total API requests received", MetricUnit.REQUESTS,
                label_keys=("method", "route", "status", "tenant"),
            ),
            MetricDefinition(
                "pmos.api.request_latency_ms", MetricKind.HISTOGRAM,
                "API request handling latency", MetricUnit.MILLISECONDS,
                label_keys=("method", "route", "tenant"),
                buckets=DEFAULT_LATENCY_BUCKETS_MS,
            ),
            MetricDefinition(
                "pmos.api.errors_total", MetricKind.COUNTER,
                "Total API errors", MetricUnit.COUNT,
                label_keys=("route", "error_type", "tenant"),
            ),
            MetricDefinition(
                "pmos.api.in_flight", MetricKind.GAUGE,
                "In-flight API requests", MetricUnit.COUNT,
                label_keys=("tenant",),
            ),
            # ---- Workflow orchestration (S2.2) ----
            MetricDefinition(
                "pmos.workflow.duration_ms", MetricKind.HISTOGRAM,
                "End-to-end workflow execution time", MetricUnit.MILLISECONDS,
                label_keys=("workflow", "status", "tenant"),
                buckets=DEFAULT_LATENCY_BUCKETS_MS,
            ),
            MetricDefinition(
                "pmos.workflow.step_duration_ms", MetricKind.HISTOGRAM,
                "Per-step workflow execution time", MetricUnit.MILLISECONDS,
                label_keys=("workflow", "step", "status", "tenant"),
                buckets=DEFAULT_LATENCY_BUCKETS_MS,
            ),
            MetricDefinition(
                "pmos.workflow.runs_total", MetricKind.COUNTER,
                "Total workflow executions", MetricUnit.COUNT,
                label_keys=("workflow", "status", "tenant"),
            ),
            # ---- Retrieval (S1.6) ----
            MetricDefinition(
                "pmos.retrieval.latency_ms", MetricKind.HISTOGRAM,
                "Retrieval latency", MetricUnit.MILLISECONDS,
                label_keys=("strategy", "tenant"),
                buckets=DEFAULT_LATENCY_BUCKETS_MS,
            ),
            MetricDefinition(
                "pmos.retrieval.results", MetricKind.HISTOGRAM,
                "Number of candidates returned per retrieval", MetricUnit.COUNT,
                label_keys=("strategy", "tenant"),
                buckets=(1, 3, 5, 10, 20, 50, 100, 200),
            ),
            MetricDefinition(
                "pmos.retrieval.top_score", MetricKind.HISTOGRAM,
                "Top relevance score of retrieval result set", MetricUnit.RATIO,
                label_keys=("strategy", "tenant"),
                buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
            ),
            MetricDefinition(
                "pmos.retrieval.empty_total", MetricKind.COUNTER,
                "Retrievals that returned zero candidates", MetricUnit.COUNT,
                label_keys=("strategy", "tenant"),
            ),
            # ---- Generation (S1.8) ----
            MetricDefinition(
                "pmos.generation.latency_ms", MetricKind.HISTOGRAM,
                "LLM generation latency", MetricUnit.MILLISECONDS,
                label_keys=("model", "tenant"),
                buckets=DEFAULT_LATENCY_BUCKETS_MS,
            ),
            MetricDefinition(
                "pmos.generation.prompt_tokens", MetricKind.HISTOGRAM,
                "Prompt token count per generation", MetricUnit.TOKENS,
                label_keys=("model", "tenant"),
                buckets=DEFAULT_TOKEN_BUCKETS,
            ),
            MetricDefinition(
                "pmos.generation.completion_tokens", MetricKind.HISTOGRAM,
                "Completion token count per generation", MetricUnit.TOKENS,
                label_keys=("model", "tenant"),
                buckets=DEFAULT_TOKEN_BUCKETS,
            ),
            MetricDefinition(
                "pmos.generation.cost_usd", MetricKind.COUNTER,
                "Estimated generation cost", MetricUnit.USD,
                label_keys=("model", "tenant"),
            ),
            MetricDefinition(
                "pmos.generation.runs_total", MetricKind.COUNTER,
                "Total generations", MetricUnit.COUNT,
                label_keys=("model", "status", "tenant"),
            ),
            # ---- Grounding (S1.9) ----
            MetricDefinition(
                "pmos.grounding.latency_ms", MetricKind.HISTOGRAM,
                "Grounding/verification latency", MetricUnit.MILLISECONDS,
                label_keys=("tenant",),
                buckets=DEFAULT_LATENCY_BUCKETS_MS,
            ),
            MetricDefinition(
                "pmos.grounding.score", MetricKind.HISTOGRAM,
                "Grounding confidence score", MetricUnit.RATIO,
                label_keys=("tenant",),
                buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
            ),
            MetricDefinition(
                "pmos.grounding.unsupported_claims", MetricKind.HISTOGRAM,
                "Count of unsupported claims detected per response", MetricUnit.COUNT,
                label_keys=("tenant",),
                buckets=(0, 1, 2, 3, 5, 8, 13),
            ),
            # ---- Ingestion (S1.1 - S1.5) ----
            MetricDefinition(
                "pmos.ingestion.latency_ms", MetricKind.HISTOGRAM,
                "Per-document ingestion latency", MetricUnit.MILLISECONDS,
                label_keys=("stage", "connector", "tenant"),
                buckets=DEFAULT_LATENCY_BUCKETS_MS,
            ),
            MetricDefinition(
                "pmos.ingestion.documents_total", MetricKind.COUNTER,
                "Documents processed", MetricUnit.COUNT,
                label_keys=("stage", "connector", "status", "tenant"),
            ),
            MetricDefinition(
                "pmos.ingestion.chunks_total", MetricKind.COUNTER,
                "Chunks produced during processing", MetricUnit.COUNT,
                label_keys=("connector", "tenant"),
            ),
            # ---- Token / cost rollups ----
            MetricDefinition(
                "pmos.tokens_total", MetricKind.COUNTER,
                "Total tokens consumed across all operations", MetricUnit.TOKENS,
                label_keys=("model", "kind", "tenant"),
            ),
            MetricDefinition(
                "pmos.cost_usd_total", MetricKind.COUNTER,
                "Total estimated spend", MetricUnit.USD,
                label_keys=("model", "tenant"),
            ),
        ]
    )
    return registry


__all__ = [
    "MetricKind",
    "MetricUnit",
    "MetricDefinition",
    "MetricsRegistry",
    "build_default_registry",
    "DEFAULT_LATENCY_BUCKETS_MS",
    "DEFAULT_TOKEN_BUCKETS",
]
