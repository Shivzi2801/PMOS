"""Observability for the extraction engine.

Defines a backend-agnostic ``MetricsSink`` plus the canonical metric names
required by Slice 1.3. The default ``InMemoryMetricsSink`` is dependency-free
and used by tests; production deployments inject a Prometheus/OTel-backed sink
implementing the same interface.

Required metrics:
  * extraction_documents_total   (counter)  — documents processed
  * extraction_atoms_total       (counter)  — atoms emitted (post-ranking)
  * extraction_failures_total    (counter)  — terminal/handled failures
  * extraction_latency_ms        (histogram)— end-to-end pipeline latency
"""

from __future__ import annotations

import abc
from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Tuple

# Canonical metric names.
M_DOCUMENTS_TOTAL = "extraction_documents_total"
M_ATOMS_TOTAL = "extraction_atoms_total"
M_FAILURES_TOTAL = "extraction_failures_total"
M_LATENCY_MS = "extraction_latency_ms"


def _labels_key(labels: Optional[Mapping[str, str]]) -> Tuple[Tuple[str, str], ...]:
    if not labels:
        return tuple()
    return tuple(sorted(labels.items()))


class MetricsSink(abc.ABC):
    """Backend-agnostic metrics interface."""

    @abc.abstractmethod
    def increment(
        self, name: str, value: float = 1.0, labels: Optional[Mapping[str, str]] = None
    ) -> None:
        ...

    @abc.abstractmethod
    def observe(
        self, name: str, value: float, labels: Optional[Mapping[str, str]] = None
    ) -> None:
        ...


class NullMetricsSink(MetricsSink):
    """No-op sink. Safe default when observability is not configured."""

    def increment(self, name, value=1.0, labels=None) -> None:  # noqa: D401
        return None

    def observe(self, name, value, labels=None) -> None:
        return None


class InMemoryMetricsSink(MetricsSink):
    """In-process sink for tests and local diagnostics."""

    def __init__(self) -> None:
        self.counters: Dict[Tuple[str, Tuple], float] = defaultdict(float)
        self.observations: Dict[Tuple[str, Tuple], List[float]] = defaultdict(list)

    def increment(self, name, value=1.0, labels=None) -> None:
        self.counters[(name, _labels_key(labels))] += value

    def observe(self, name, value, labels=None) -> None:
        self.observations[(name, _labels_key(labels))].append(value)

    # --- test/diagnostic helpers ---
    def counter_value(self, name: str, labels: Optional[Mapping[str, str]] = None) -> float:
        return self.counters.get((name, _labels_key(labels)), 0.0)

    def observation_count(self, name: str, labels: Optional[Mapping[str, str]] = None) -> int:
        return len(self.observations.get((name, _labels_key(labels)), []))
