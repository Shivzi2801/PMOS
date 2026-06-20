"""
PMOS Observability & Monitoring — Error Hierarchy (S2.6)

All exceptions raised by the observability subsystem derive from
``ObservabilityError``. Callers can catch the base type to handle any
failure originating from this slice, or catch a specific subtype for
fine-grained recovery.

Design notes
------------
* Errors carry structured context (a ``details`` mapping) so they can be
  serialized into telemetry events without losing information.
* Observability must *never* take down the host application. The service
  layer is expected to swallow these errors at the boundary and degrade
  gracefully; the exceptions exist primarily for internal control flow,
  testing, and diagnostics.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class ObservabilityError(Exception):
    """Base class for every error raised by the observability subsystem."""

    def __init__(
        self,
        message: str,
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.details:
            return f"{self.message} | details={self.details!r}"
        return self.message


class MetricError(ObservabilityError):
    """Raised for invalid metric definitions or recording operations."""


class MetricNotRegisteredError(MetricError):
    """Raised when recording against a metric that was never registered."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Metric '{name}' is not registered",
            details={"metric": name},
        )
        self.metric_name = name


class DuplicateMetricError(MetricError):
    """Raised when registering a metric name that already exists with a
    conflicting definition."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Metric '{name}' is already registered with a different definition",
            details={"metric": name},
        )
        self.metric_name = name


class MetricTypeError(MetricError):
    """Raised when an operation is incompatible with a metric's kind
    (e.g. observing a value on a counter)."""


class LabelError(MetricError):
    """Raised when label keys do not match a metric's declared label schema."""


class TelemetryError(ObservabilityError):
    """Base class for telemetry pipeline failures."""


class SinkError(TelemetryError):
    """Raised when a telemetry sink fails to accept an event."""

    def __init__(self, sink_name: str, cause: Optional[BaseException] = None) -> None:
        super().__init__(
            f"Telemetry sink '{sink_name}' failed to emit event",
            details={"sink": sink_name, "cause": repr(cause) if cause else None},
        )
        self.sink_name = sink_name
        self.cause = cause


class TracingError(ObservabilityError):
    """Base class for tracing failures."""


class SpanError(TracingError):
    """Raised for invalid span lifecycle operations (e.g. ending twice)."""


class TraceContextError(TracingError):
    """Raised when trace context propagation fails (malformed headers, etc.)."""


class AlertError(ObservabilityError):
    """Base class for alerting failures."""


class InvalidAlertRuleError(AlertError):
    """Raised when an alert rule definition is invalid."""


class HealthError(ObservabilityError):
    """Base class for health monitoring failures."""


class UnknownComponentError(HealthError):
    """Raised when querying health for a component that was never registered."""

    def __init__(self, component: str) -> None:
        super().__init__(
            f"Component '{component}' is not registered with the health aggregator",
            details={"component": component},
        )
        self.component = component


class ReportingError(ObservabilityError):
    """Base class for reporting / projection failures."""


class CostTrackingError(ObservabilityError):
    """Raised when cost or token accounting fails (e.g. unknown model)."""


__all__ = [
    "ObservabilityError",
    "MetricError",
    "MetricNotRegisteredError",
    "DuplicateMetricError",
    "MetricTypeError",
    "LabelError",
    "TelemetryError",
    "SinkError",
    "TracingError",
    "SpanError",
    "TraceContextError",
    "AlertError",
    "InvalidAlertRuleError",
    "HealthError",
    "UnknownComponentError",
    "ReportingError",
    "CostTrackingError",
]
