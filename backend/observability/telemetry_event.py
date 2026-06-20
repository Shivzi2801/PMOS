"""
PMOS Observability & Monitoring — Telemetry Event (S2.6)

A telemetry event is a structured, immutable record of *something that
happened*: a workflow completing, a retrieval failing, an alert firing, a span
closing. Events are the unit of the telemetry pipeline; sinks consume them.

Events are deliberately richer than metrics. A metric answers "how many / how
long"; an event answers "what exactly happened, in what context, correlated to
which trace/tenant/workflow".
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class EventSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: Dict["EventSeverity", int] = {
    EventSeverity.DEBUG: 10,
    EventSeverity.INFO: 20,
    EventSeverity.WARNING: 30,
    EventSeverity.ERROR: 40,
    EventSeverity.CRITICAL: 50,
}


class EventCategory(str, Enum):
    """Coarse classification used for routing and filtering."""

    METRIC = "metric"
    TRACE = "trace"
    HEALTH = "health"
    ALERT = "alert"
    AUDIT = "audit"
    LIFECYCLE = "lifecycle"
    USAGE = "usage"
    SYSTEM = "system"


# Standard correlation dimensions carried by every event. Centralizing these as
# explicit fields (rather than free-form attributes) lets the pipeline correlate
# events across slices without string-key guessing.
@dataclass(frozen=True)
class EventContext:
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    workflow_id: Optional[str] = None
    request_id: Optional[str] = None
    component: Optional[str] = None

    def merge(self, other: "EventContext") -> "EventContext":
        """Overlay non-None fields of ``other`` onto ``self``."""
        return EventContext(
            tenant_id=other.tenant_id or self.tenant_id,
            user_id=other.user_id or self.user_id,
            trace_id=other.trace_id or self.trace_id,
            span_id=other.span_id or self.span_id,
            workflow_id=other.workflow_id or self.workflow_id,
            request_id=other.request_id or self.request_id,
            component=other.component or self.component,
        )

    def as_dict(self) -> Dict[str, str]:
        return {
            k: v
            for k, v in {
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "trace_id": self.trace_id,
                "span_id": self.span_id,
                "workflow_id": self.workflow_id,
                "request_id": self.request_id,
                "component": self.component,
            }.items()
            if v is not None
        }


@dataclass(frozen=True)
class TelemetryEvent:
    """An immutable telemetry record.

    Attributes
    ----------
    name:
        Dotted event name, e.g. ``workflow.completed`` or ``alert.fired``.
    category:
        :class:`EventCategory` for routing.
    severity:
        :class:`EventSeverity`.
    timestamp:
        Epoch seconds when the event occurred.
    event_id:
        Unique identifier (UUID4 hex) — useful for dedup at sinks.
    context:
        Correlation dimensions.
    attributes:
        Arbitrary, JSON-serializable payload specific to the event.
    """

    name: str
    category: EventCategory
    severity: EventSeverity = EventSeverity.INFO
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    context: EventContext = field(default_factory=EventContext)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def with_context(self, ctx: EventContext) -> "TelemetryEvent":
        """Return a copy with ``ctx`` merged onto the existing context."""
        return replace(self, context=self.context.merge(ctx))

    def with_attributes(self, **extra: Any) -> "TelemetryEvent":
        merged = dict(self.attributes)
        merged.update(extra)
        return replace(self, attributes=merged)

    def to_dict(self) -> Dict[str, Any]:
        """Flatten to a JSON-serializable dict (sink-friendly)."""
        return {
            "event_id": self.event_id,
            "name": self.name,
            "category": self.category.value,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "context": self.context.as_dict(),
            "attributes": dict(self.attributes),
        }


__all__ = [
    "EventSeverity",
    "EventCategory",
    "EventContext",
    "TelemetryEvent",
]
