"""
PMOS Observability & Monitoring — Metrics Collector (S2.6)

The collector is the runtime engine that accumulates metric values in memory.
It is the write-side counterpart to :class:`MetricsRegistry` (the schema) and
:class:`MetricsSnapshot` (the read-side view).

Responsibilities
-----------------
* Validate every record call against the registry (kind + label schema).
* Maintain thread-safe, lock-striped accumulators for counters, gauges and
  histograms.
* Produce immutable snapshots on demand.

It is deliberately free of any I/O. Exporting is the job of telemetry sinks;
the collector only holds state.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from .errors import MetricTypeError
from .metrics_registry import (
    MetricDefinition,
    MetricKind,
    MetricsRegistry,
)
from .metrics_snapshot import (
    HistogramState,
    LabelSet,
    MetricSample,
    MetricsSnapshot,
    normalize_labels,
)


@dataclass
class _CounterAcc:
    value: float = 0.0


@dataclass
class _GaugeAcc:
    value: float = 0.0


@dataclass
class _HistogramAcc:
    bounds: tuple
    counts: List[int]
    count: int = 0
    sum: float = 0.0
    min: Optional[float] = None
    max: Optional[float] = None

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        self.min = value if self.min is None else min(self.min, value)
        self.max = value if self.max is None else max(self.max, value)
        for i, upper in enumerate(self.bounds):
            if value <= upper:
                self.counts[i] += 1
                return
        # Overflow into the implicit +Inf bucket (last slot).
        self.counts[-1] += 1


class _MetricState:
    """Holds all per-label-set accumulators for a single metric name."""

    __slots__ = ("definition", "lock", "counters", "gauges", "histograms")

    def __init__(self, definition: MetricDefinition) -> None:
        self.definition = definition
        self.lock = threading.Lock()
        self.counters: Dict[LabelSet, _CounterAcc] = {}
        self.gauges: Dict[LabelSet, _GaugeAcc] = {}
        self.histograms: Dict[LabelSet, _HistogramAcc] = {}

    def _hist_bounds(self) -> tuple:
        # Append +Inf as the implicit overflow bound so counts line up.
        return tuple(self.definition.buckets) + (float("inf"),)


class MetricsCollector:
    """Thread-safe, in-memory metrics engine.

    Parameters
    ----------
    registry:
        The :class:`MetricsRegistry` that defines valid metrics. Every record
        call validates against it.
    clock:
        Callable returning epoch seconds. Injectable for deterministic tests.
    """

    def __init__(
        self,
        registry: MetricsRegistry,
        *,
        clock=time.time,
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._states_lock = threading.RLock()
        self._states: Dict[str, _MetricState] = {}

    # -- internal helpers -------------------------------------------------

    def _state(self, name: str) -> _MetricState:
        with self._states_lock:
            state = self._states.get(name)
            if state is None:
                definition = self._registry.get(name)  # raises if unknown
                state = _MetricState(definition)
                self._states[name] = state
            return state

    @staticmethod
    def _check_kind(definition: MetricDefinition, expected: MetricKind) -> None:
        if definition.kind is not expected:
            raise MetricTypeError(
                f"Operation requires a {expected.value} but "
                f"'{definition.name}' is a {definition.kind.value}",
                details={"metric": definition.name},
            )

    # -- write API --------------------------------------------------------

    def increment(
        self,
        name: str,
        *,
        labels: Optional[Mapping[str, str]] = None,
        amount: float = 1.0,
    ) -> None:
        """Add ``amount`` to a counter. ``amount`` must be non-negative."""
        if amount < 0:
            raise MetricTypeError(
                "Counter increments must be non-negative",
                details={"metric": name, "amount": amount},
            )
        labels = labels or {}
        state = self._state(name)
        self._check_kind(state.definition, MetricKind.COUNTER)
        state.definition.validate_labels(labels)
        key = normalize_labels(labels)
        with state.lock:
            acc = state.counters.get(key)
            if acc is None:
                acc = _CounterAcc()
                state.counters[key] = acc
            acc.value += amount

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Set a gauge to an absolute value."""
        labels = labels or {}
        state = self._state(name)
        self._check_kind(state.definition, MetricKind.GAUGE)
        state.definition.validate_labels(labels)
        key = normalize_labels(labels)
        with state.lock:
            acc = state.gauges.get(key)
            if acc is None:
                acc = _GaugeAcc()
                state.gauges[key] = acc
            acc.value = value

    def adjust_gauge(
        self,
        name: str,
        delta: float,
        *,
        labels: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Add (or subtract) ``delta`` from a gauge (e.g. in-flight counters)."""
        labels = labels or {}
        state = self._state(name)
        self._check_kind(state.definition, MetricKind.GAUGE)
        state.definition.validate_labels(labels)
        key = normalize_labels(labels)
        with state.lock:
            acc = state.gauges.get(key)
            if acc is None:
                acc = _GaugeAcc()
                state.gauges[key] = acc
            acc.value += delta

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Record a single observation into a histogram."""
        labels = labels or {}
        state = self._state(name)
        self._check_kind(state.definition, MetricKind.HISTOGRAM)
        state.definition.validate_labels(labels)
        key = normalize_labels(labels)
        with state.lock:
            acc = state.histograms.get(key)
            if acc is None:
                bounds = state._hist_bounds()
                acc = _HistogramAcc(bounds=bounds, counts=[0] * len(bounds))
                state.histograms[key] = acc
            acc.observe(value)

    # -- read API ---------------------------------------------------------

    def snapshot(self) -> MetricsSnapshot:
        """Capture an immutable view of all metric state."""
        samples: List[MetricSample] = []
        with self._states_lock:
            states = list(self._states.values())

        for state in states:
            definition = state.definition
            with state.lock:
                for key, c in state.counters.items():
                    samples.append(
                        MetricSample(
                            name=definition.name,
                            kind=MetricKind.COUNTER,
                            unit=definition.unit,
                            labels=key,
                            value=c.value,
                        )
                    )
                for key, g in state.gauges.items():
                    samples.append(
                        MetricSample(
                            name=definition.name,
                            kind=MetricKind.GAUGE,
                            unit=definition.unit,
                            labels=key,
                            value=g.value,
                        )
                    )
                for key, h in state.histograms.items():
                    samples.append(
                        MetricSample(
                            name=definition.name,
                            kind=MetricKind.HISTOGRAM,
                            unit=definition.unit,
                            labels=key,
                            histogram=HistogramState(
                                bucket_bounds=tuple(h.bounds),
                                bucket_counts=tuple(h.counts),
                                count=h.count,
                                sum=h.sum,
                                min=h.min,
                                max=h.max,
                            ),
                        )
                    )
        return MetricsSnapshot(captured_at=self._clock(), samples=tuple(samples))

    def reset(self) -> None:
        """Drop all accumulated state. Primarily for tests / hot reloads."""
        with self._states_lock:
            self._states.clear()


__all__ = ["MetricsCollector"]
