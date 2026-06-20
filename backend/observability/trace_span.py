"""
PMOS Observability & Monitoring — Trace Span (S2.6)

A :class:`TraceSpan` represents a single timed operation within a trace: a
retrieval call, an LLM generation, a workflow step. Spans form a tree via
parent span ids and together describe the causal/temporal structure of a
request.

Spans are mutable while *active* (attributes/events can be added, status set)
and produce an immutable :class:`SpanData` record when ended. The tracer
collects these records.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import SpanError
from .trace_context import TraceContext, generate_span_id


class SpanKind(str, Enum):
    INTERNAL = "internal"
    SERVER = "server"      # inbound request handling
    CLIENT = "client"      # outbound call (LLM, vector store)
    PRODUCER = "producer"  # enqueue
    CONSUMER = "consumer"  # dequeue


class SpanStatus(str, Enum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True)
class SpanEvent:
    """A timestamped annotation within a span (e.g. 'cache_miss')."""

    name: str
    timestamp: float
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpanData:
    """Immutable record emitted when a span ends."""

    name: str
    kind: SpanKind
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    start_time: float
    end_time: float
    status: SpanStatus
    status_message: Optional[str]
    attributes: Mapping[str, Any]
    events: Tuple[SpanEvent, ...]
    tenant_id: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "status_message": self.status_message,
            "attributes": dict(self.attributes),
            "tenant_id": self.tenant_id,
            "events": [
                {"name": e.name, "timestamp": e.timestamp, "attributes": dict(e.attributes)}
                for e in self.events
            ],
        }


class TraceSpan:
    """A mutable, active span.

    Spans are typically used as context managers via the tracer, which handles
    activation and automatic error capture. They may also be driven manually.
    """

    __slots__ = (
        "_name", "_kind", "_context", "_parent_span_id", "_start_time",
        "_end_time", "_status", "_status_message", "_attributes", "_events",
        "_tenant_id", "_lock", "_ended", "_on_end", "_clock",
    )

    def __init__(
        self,
        name: str,
        *,
        context: TraceContext,
        parent_span_id: Optional[str],
        kind: SpanKind = SpanKind.INTERNAL,
        tenant_id: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
        on_end=None,
        clock=time.time,
    ) -> None:
        self._name = name
        self._kind = kind
        self._context = context
        self._parent_span_id = parent_span_id
        self._clock = clock
        self._start_time = clock()
        self._end_time: Optional[float] = None
        self._status = SpanStatus.UNSET
        self._status_message: Optional[str] = None
        self._attributes: Dict[str, Any] = dict(attributes or {})
        self._events: List[SpanEvent] = []
        self._tenant_id = tenant_id or context.baggage.get("tenant_id")
        self._lock = threading.Lock()
        self._ended = False
        self._on_end = on_end

    # -- identity ---------------------------------------------------------

    @property
    def context(self) -> TraceContext:
        return self._context

    @property
    def trace_id(self) -> str:
        return self._context.trace_id

    @property
    def span_id(self) -> str:
        return self._context.span_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_recording(self) -> bool:
        return not self._ended and self._context.sampled

    # -- mutation ---------------------------------------------------------

    def set_attribute(self, key: str, value: Any) -> "TraceSpan":
        with self._lock:
            if not self._ended:
                self._attributes[key] = value
        return self

    def set_attributes(self, attributes: Mapping[str, Any]) -> "TraceSpan":
        with self._lock:
            if not self._ended:
                self._attributes.update(attributes)
        return self

    def add_event(
        self, name: str, attributes: Optional[Mapping[str, Any]] = None
    ) -> "TraceSpan":
        with self._lock:
            if not self._ended:
                self._events.append(
                    SpanEvent(name=name, timestamp=self._clock(),
                              attributes=dict(attributes or {}))
                )
        return self

    def set_status(self, status: SpanStatus, message: Optional[str] = None) -> "TraceSpan":
        with self._lock:
            if not self._ended:
                self._status = status
                self._status_message = message
        return self

    def record_exception(self, exc: BaseException) -> "TraceSpan":
        self.add_event(
            "exception",
            {"exception.type": type(exc).__name__, "exception.message": str(exc)},
        )
        self.set_status(SpanStatus.ERROR, str(exc))
        return self

    # -- lifecycle --------------------------------------------------------

    def end(self) -> SpanData:
        """Finalize the span and return its immutable record.

        Raises :class:`SpanError` if ended twice.
        """
        with self._lock:
            if self._ended:
                raise SpanError(
                    f"Span '{self._name}' already ended",
                    details={"span_id": self.span_id},
                )
            self._ended = True
            self._end_time = self._clock()
            if self._status is SpanStatus.UNSET:
                self._status = SpanStatus.OK
            data = SpanData(
                name=self._name,
                kind=self._kind,
                trace_id=self._context.trace_id,
                span_id=self._context.span_id,
                parent_span_id=self._parent_span_id,
                start_time=self._start_time,
                end_time=self._end_time,
                status=self._status,
                status_message=self._status_message,
                attributes=dict(self._attributes),
                events=tuple(self._events),
                tenant_id=self._tenant_id,
            )
        if self._on_end is not None:
            self._on_end(data)
        return data

    # -- context manager --------------------------------------------------

    def __enter__(self) -> "TraceSpan":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.record_exception(exc)
        if not self._ended:
            self.end()
        return False  # never suppress


def new_child_context(parent: TraceContext) -> TraceContext:
    return parent.child(generate_span_id())


__all__ = [
    "SpanKind",
    "SpanStatus",
    "SpanEvent",
    "SpanData",
    "TraceSpan",
    "new_child_context",
]
