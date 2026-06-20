"""
PMOS Observability & Monitoring — Distributed Tracer (S2.6)

The tracer is the factory and lifecycle manager for spans. It:

* starts root spans (from inbound context or fresh) and child spans,
* maintains the *active* span per execution context using
  :class:`contextvars.ContextVar` (async/thread safe),
* emits a :class:`TelemetryEvent` and records a span-duration metric whenever a
  span ends,
* exposes propagation helpers (inject/extract) for cross-service boundaries.

It depends only on abstractions: a span processor callback receives finished
:class:`SpanData`. The :class:`~pmos.observability.observability_service.ObservabilityService`
wires this to the telemetry sink and metrics collector.
"""

from __future__ import annotations

import contextlib
import time
from contextvars import ContextVar, Token
from typing import Any, Callable, Iterator, Mapping, Optional

from .telemetry_event import (
    EventCategory,
    EventContext,
    EventSeverity,
    TelemetryEvent,
)
from .trace_context import TraceContext
from .trace_span import SpanData, SpanKind, SpanStatus, TraceSpan

# The active context (current span position) for the running task/thread.
_ACTIVE_CONTEXT: ContextVar[Optional[TraceContext]] = ContextVar(
    "pmos_active_trace_context", default=None
)

SpanProcessor = Callable[[SpanData], None]


class DistributedTracer:
    """Creates and tracks spans across a distributed PMOS request.

    Parameters
    ----------
    service_name:
        Logical service identity stamped on every span.
    processor:
        Optional callback invoked with each finished :class:`SpanData`.
    event_emitter:
        Optional callback invoked with a :class:`TelemetryEvent` per finished
        span (category=TRACE). Wired to the telemetry pipeline by the service.
    sampler:
        Optional predicate ``(name, parent_context) -> bool`` deciding whether
        a new root trace is sampled. Defaults to always-on.
    clock:
        Injectable epoch-seconds clock for deterministic tests.
    """

    def __init__(
        self,
        service_name: str,
        *,
        processor: Optional[SpanProcessor] = None,
        event_emitter: Optional[Callable[[TelemetryEvent], None]] = None,
        sampler: Optional[Callable[[str, Optional[TraceContext]], bool]] = None,
        clock=time.time,
    ) -> None:
        self._service_name = service_name
        self._processor = processor
        self._event_emitter = event_emitter
        self._sampler = sampler
        self._clock = clock

    # -- active context ---------------------------------------------------

    @staticmethod
    def current_context() -> Optional[TraceContext]:
        return _ACTIVE_CONTEXT.get()

    # -- span creation ----------------------------------------------------

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[TraceContext] = None,
        tenant_id: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> TraceSpan:
        """Create a span (not yet activated). Caller is responsible for ending
        it. Prefer :meth:`span` for scoped usage."""
        parent_ctx = parent if parent is not None else _ACTIVE_CONTEXT.get()

        if parent_ctx is None:
            sampled = True if self._sampler is None else self._sampler(name, None)
            context = TraceContext.new_root(sampled=sampled)
            parent_span_id = None
        else:
            context = parent_ctx.child()
            parent_span_id = parent_ctx.span_id

        merged_attrs = {"service.name": self._service_name}
        if attributes:
            merged_attrs.update(attributes)

        return TraceSpan(
            name,
            context=context,
            parent_span_id=parent_span_id,
            kind=kind,
            tenant_id=tenant_id,
            attributes=merged_attrs,
            on_end=self._on_span_end,
            clock=self._clock,
        )

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[TraceContext] = None,
        tenant_id: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[TraceSpan]:
        """Scoped span: activates the span's context for the duration of the
        ``with`` block, captures exceptions, and ends it automatically."""
        span = self.start_span(
            name, kind=kind, parent=parent, tenant_id=tenant_id, attributes=attributes
        )
        token: Token = _ACTIVE_CONTEXT.set(span.context)
        try:
            yield span
        except BaseException as exc:  # noqa: BLE001 - record then re-raise
            span.record_exception(exc)
            raise
        finally:
            _ACTIVE_CONTEXT.reset(token)
            if span.is_recording or not _span_already_ended(span):
                with contextlib.suppress(Exception):
                    span.end()

    # -- propagation ------------------------------------------------------

    def inject(self, context: Optional[TraceContext] = None) -> dict:
        """Serialize the active (or supplied) context into carrier headers."""
        ctx = context or _ACTIVE_CONTEXT.get()
        if ctx is None:
            return {}
        return ctx.to_headers()

    def extract(self, headers: Mapping[str, str]) -> Optional[TraceContext]:
        """Parse inbound headers into a :class:`TraceContext` (or None)."""
        return TraceContext.from_headers(headers)

    @contextlib.contextmanager
    def continue_from_headers(
        self,
        headers: Mapping[str, str],
        name: str,
        *,
        kind: SpanKind = SpanKind.SERVER,
        tenant_id: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[TraceSpan]:
        """Convenience: extract inbound context and open a server span."""
        parent = self.extract(headers)
        with self.span(
            name, kind=kind, parent=parent, tenant_id=tenant_id, attributes=attributes
        ) as span:
            yield span

    # -- internal ---------------------------------------------------------

    def _on_span_end(self, data: SpanData) -> None:
        if self._processor is not None:
            with contextlib.suppress(Exception):
                self._processor(data)
        if self._event_emitter is not None:
            severity = (
                EventSeverity.ERROR
                if data.status is SpanStatus.ERROR
                else EventSeverity.DEBUG
            )
            event = TelemetryEvent(
                name=f"span.{data.name}",
                category=EventCategory.TRACE,
                severity=severity,
                timestamp=data.end_time,
                context=EventContext(
                    tenant_id=data.tenant_id,
                    trace_id=data.trace_id,
                    span_id=data.span_id,
                    component=self._service_name,
                ),
                attributes={
                    "duration_ms": data.duration_ms,
                    "status": data.status.value,
                    "kind": data.kind.value,
                    **dict(data.attributes),
                },
            )
            with contextlib.suppress(Exception):
                self._event_emitter(event)


def _span_already_ended(span: TraceSpan) -> bool:
    # TraceSpan exposes is_recording; an ended sampled span reports False.
    return not span.is_recording


__all__ = ["DistributedTracer", "SpanProcessor"]
