"""
PMOS Observability & Monitoring — Telemetry Sinks (S2.6)

A *sink* is a destination for :class:`TelemetryEvent` objects. The subsystem
ships only in-process sinks (no external services, per design constraints):

* :class:`InMemoryTelemetrySink` — bounded ring buffer, queryable; ideal for
  tests, debugging, and the dashboard projection.
* :class:`LoggingTelemetrySink` — bridges events onto the standard library
  ``logging`` module.
* :class:`CompositeTelemetrySink` — fans out to multiple sinks, isolating
  failures so one bad sink cannot starve the others.
* :class:`FilteringTelemetrySink` — wraps another sink, dropping events below
  a severity floor or outside a category allow-list.

Production deployments inject their own sink implementing
:class:`TelemetrySink` (e.g. an OTLP exporter) without touching the rest of the
subsystem — this is the dependency-injection seam.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections import deque
from typing import Callable, Deque, Iterable, List, Optional, Sequence, Tuple

from .errors import SinkError
from .telemetry_event import EventCategory, EventSeverity, TelemetryEvent


class TelemetrySink(ABC):
    """Abstract destination for telemetry events."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def emit(self, event: TelemetryEvent) -> None:
        """Accept a single event. Implementations should be fast and must not
        raise for routine backpressure — drop or buffer instead. Raising is
        reserved for genuine, unexpected failures and will be wrapped in a
        :class:`SinkError` by composite sinks."""

    def emit_batch(self, events: Iterable[TelemetryEvent]) -> None:
        for event in events:
            self.emit(event)

    def flush(self) -> None:  # pragma: no cover - default no-op
        """Force any buffered events to their destination."""

    def close(self) -> None:  # pragma: no cover - default no-op
        """Release resources."""


class InMemoryTelemetrySink(TelemetrySink):
    """Thread-safe bounded buffer of recent events.

    Acts as both a test double and the backing store for in-process queries
    (dashboard projection, recent-error inspection).
    """

    def __init__(self, capacity: int = 10_000) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._buffer: Deque[TelemetryEvent] = deque(maxlen=capacity)
        self._total_emitted = 0
        self._total_dropped = 0

    def emit(self, event: TelemetryEvent) -> None:
        with self._lock:
            if len(self._buffer) == self._capacity:
                self._total_dropped += 1
            self._buffer.append(event)
            self._total_emitted += 1

    def events(self) -> Tuple[TelemetryEvent, ...]:
        with self._lock:
            return tuple(self._buffer)

    def query(
        self,
        *,
        category: Optional[EventCategory] = None,
        min_severity: Optional[EventSeverity] = None,
        tenant_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        name_prefix: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Tuple[TelemetryEvent, ...]:
        """Filter buffered events by common dimensions (most recent first)."""
        with self._lock:
            items = list(self._buffer)
        results: List[TelemetryEvent] = []
        for ev in reversed(items):
            if category is not None and ev.category is not category:
                continue
            if min_severity is not None and ev.severity.rank < min_severity.rank:
                continue
            if tenant_id is not None and ev.context.tenant_id != tenant_id:
                continue
            if trace_id is not None and ev.context.trace_id != trace_id:
                continue
            if name_prefix is not None and not ev.name.startswith(name_prefix):
                continue
            results.append(ev)
            if limit is not None and len(results) >= limit:
                break
        return tuple(results)

    @property
    def total_emitted(self) -> int:
        with self._lock:
            return self._total_emitted

    @property
    def total_dropped(self) -> int:
        with self._lock:
            return self._total_dropped

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


_PY_LEVEL = {
    EventSeverity.DEBUG: logging.DEBUG,
    EventSeverity.INFO: logging.INFO,
    EventSeverity.WARNING: logging.WARNING,
    EventSeverity.ERROR: logging.ERROR,
    EventSeverity.CRITICAL: logging.CRITICAL,
}


class LoggingTelemetrySink(TelemetrySink):
    """Bridge events onto the stdlib ``logging`` framework."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger("pmos.observability")

    def emit(self, event: TelemetryEvent) -> None:
        level = _PY_LEVEL.get(event.severity, logging.INFO)
        self._logger.log(
            level,
            "%s",
            event.name,
            extra={"telemetry": event.to_dict()},
        )


class FilteringTelemetrySink(TelemetrySink):
    """Decorator sink applying a severity floor and/or category allow-list."""

    def __init__(
        self,
        inner: TelemetrySink,
        *,
        min_severity: Optional[EventSeverity] = None,
        categories: Optional[Sequence[EventCategory]] = None,
        predicate: Optional[Callable[[TelemetryEvent], bool]] = None,
    ) -> None:
        self._inner = inner
        self._min_severity = min_severity
        self._categories = set(categories) if categories else None
        self._predicate = predicate

    @property
    def name(self) -> str:
        return f"Filtering({self._inner.name})"

    def _allowed(self, event: TelemetryEvent) -> bool:
        if self._min_severity and event.severity.rank < self._min_severity.rank:
            return False
        if self._categories is not None and event.category not in self._categories:
            return False
        if self._predicate is not None and not self._predicate(event):
            return False
        return True

    def emit(self, event: TelemetryEvent) -> None:
        if self._allowed(event):
            self._inner.emit(event)

    def flush(self) -> None:
        self._inner.flush()

    def close(self) -> None:
        self._inner.close()


class CompositeTelemetrySink(TelemetrySink):
    """Fan-out sink that isolates failures across children.

    If a child raises, the error is captured and forwarded to an optional
    ``on_error`` callback, then the next child is still attempted. This makes
    the telemetry path resilient: a misbehaving exporter cannot take down the
    application or block other exporters.
    """

    def __init__(
        self,
        sinks: Sequence[TelemetrySink],
        *,
        on_error: Optional[Callable[[SinkError], None]] = None,
    ) -> None:
        self._sinks: List[TelemetrySink] = list(sinks)
        self._on_error = on_error

    def add(self, sink: TelemetrySink) -> None:
        self._sinks.append(sink)

    def emit(self, event: TelemetryEvent) -> None:
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception as exc:  # noqa: BLE001 - isolation is intentional
                err = SinkError(sink.name, cause=exc)
                if self._on_error is not None:
                    self._on_error(err)

    def flush(self) -> None:
        for sink in self._sinks:
            try:
                sink.flush()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        for sink in self._sinks:
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "TelemetrySink",
    "InMemoryTelemetrySink",
    "LoggingTelemetrySink",
    "FilteringTelemetrySink",
    "CompositeTelemetrySink",
]
