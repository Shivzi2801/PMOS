"""
PMOS S1.5 — Index Fan-Out
metrics.py

Metrics surface for the indexing subsystem.

This is a thin, dependency-free metrics sink modeled on the counter/histogram
pattern used elsewhere in PMOS (S1.1 sync-run tracking emitted the same shape).
A real deployment swaps `InMemoryMetricsSink` for a StatsD/OpenTelemetry
adapter implementing the same `MetricsSink` protocol.

Tracked (per slice requirement #8):
  * chunks_created           (counter)
  * chunks_deduplicated      (counter)
  * chunks_indexed           (counter)
  * reconciliation_failures  (counter)
  * indexing_latency_ms      (histogram)

All metrics are labeled with tenant_id at minimum so per-tenant dashboards and
quota enforcement are possible. Labels never include chunk content or ACL
principals.
"""

from __future__ import annotations

import abc
import dataclasses
import time
from collections import defaultdict
from typing import Dict, List, Mapping, Tuple

CHUNKS_CREATED = "chunks_created"
CHUNKS_DEDUPLICATED = "chunks_deduplicated"
CHUNKS_INDEXED = "chunks_indexed"
RECONCILIATION_FAILURES = "reconciliation_failures"
INDEXING_LATENCY_MS = "indexing_latency_ms"

_LabelKey = Tuple[Tuple[str, str], ...]


def _freeze_labels(labels: Mapping[str, str]) -> _LabelKey:
    return tuple(sorted(labels.items()))


class MetricsSink(abc.ABC):
    @abc.abstractmethod
    def incr(self, name: str, value: int = 1, **labels: str) -> None:
        ...

    @abc.abstractmethod
    def observe(self, name: str, value: float, **labels: str) -> None:
        ...


@dataclasses.dataclass
class InMemoryMetricsSink(MetricsSink):
    counters: Dict[Tuple[str, _LabelKey], int] = dataclasses.field(
        default_factory=lambda: defaultdict(int)
    )
    histograms: Dict[Tuple[str, _LabelKey], List[float]] = dataclasses.field(
        default_factory=lambda: defaultdict(list)
    )

    def incr(self, name: str, value: int = 1, **labels: str) -> None:
        self.counters[(name, _freeze_labels(labels))] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        self.histograms[(name, _freeze_labels(labels))].append(value)

    # --- read helpers (tests/reports) -------------------------------------

    def counter_value(self, name: str, **labels: str) -> int:
        return self.counters.get((name, _freeze_labels(labels)), 0)

    def histogram_values(self, name: str, **labels: str) -> List[float]:
        return list(self.histograms.get((name, _freeze_labels(labels)), []))


class LatencyTimer:
    """Context manager that observes elapsed wall-clock ms into a histogram."""

    def __init__(self, sink: MetricsSink, name: str, **labels: str) -> None:
        self.sink = sink
        self.name = name
        self.labels = labels
        self._start = 0.0

    def __enter__(self) -> "LatencyTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self.sink.observe(self.name, elapsed_ms, **self.labels)
