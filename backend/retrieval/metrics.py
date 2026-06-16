"""
backend/retrieval/metrics.py

Retrieval metrics collection (S1.6, responsibility #7).

A tiny, dependency-free metrics facade following the pattern from earlier
slices: a ``MetricsSink`` Protocol the rest of the codebase programs against,
plus concrete sinks. The retriever emits a single structured ``RetrievalEvent``
per query (success or failure) and increments a handful of counters/timers.

Concrete sinks provided:
* ``NullMetrics``       -- discards everything (default; zero overhead).
* ``InMemoryMetrics``   -- accumulates counters, timers, and events for tests
                           and local inspection.

A production deployment implements ``MetricsSink`` to forward to StatsD /
OpenTelemetry / Prometheus without changing any retriever code.

No external dependencies.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable


@dataclass
class RetrievalEvent:
    """One structured record describing a single retrieval call."""

    tenant_id: str
    query_fingerprint: str
    success: bool
    took_ms: float
    candidates_fetched: int = 0
    returned: int = 0
    reranked: bool = False
    expanded: bool = False
    degraded: bool = False
    error_code: Optional[str] = None
    tags: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "query_fingerprint": self.query_fingerprint,
            "success": self.success,
            "took_ms": self.took_ms,
            "candidates_fetched": self.candidates_fetched,
            "returned": self.returned,
            "reranked": self.reranked,
            "expanded": self.expanded,
            "degraded": self.degraded,
            "error_code": self.error_code,
            "tags": dict(self.tags),
        }


@runtime_checkable
class MetricsSink(Protocol):
    """Extension point for metrics backends."""

    def incr(self, name: str, value: int = 1, *, tags: Optional[Mapping[str, Any]] = None) -> None:
        ...

    def timing(self, name: str, ms: float, *, tags: Optional[Mapping[str, Any]] = None) -> None:
        ...

    def record_event(self, event: RetrievalEvent) -> None:
        ...


class NullMetrics:
    """Default no-op sink."""

    def incr(self, name: str, value: int = 1, *, tags: Optional[Mapping[str, Any]] = None) -> None:
        return None

    def timing(self, name: str, ms: float, *, tags: Optional[Mapping[str, Any]] = None) -> None:
        return None

    def record_event(self, event: RetrievalEvent) -> None:
        return None


class InMemoryMetrics:
    """Thread-safe in-memory sink for tests and local debugging."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: Dict[str, int] = {}
        self.timers: Dict[str, List[float]] = {}
        self.events: List[RetrievalEvent] = []

    def incr(self, name: str, value: int = 1, *, tags: Optional[Mapping[str, Any]] = None) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + value

    def timing(self, name: str, ms: float, *, tags: Optional[Mapping[str, Any]] = None) -> None:
        with self._lock:
            self.timers.setdefault(name, []).append(ms)

    def record_event(self, event: RetrievalEvent) -> None:
        with self._lock:
            self.events.append(event)

    # Inspection helpers (test ergonomics) ----------------------------------
    def counter(self, name: str) -> int:
        with self._lock:
            return self.counters.get(name, 0)

    def last_event(self) -> Optional[RetrievalEvent]:
        with self._lock:
            return self.events[-1] if self.events else None


# Canonical metric names emitted by the retriever (single source of truth).
class Names:
    QUERIES = "retrieval.queries"
    SUCCESS = "retrieval.success"
    FAILURE = "retrieval.failure"
    LATENCY = "retrieval.latency_ms"
    CANDIDATES = "retrieval.candidates"
    RETURNED = "retrieval.returned"
    EMPTY = "retrieval.empty_result"
    DEGRADED = "retrieval.degraded"
    RERANKED = "retrieval.reranked"
    EXPANDED = "retrieval.expanded"
    ACL_DROPPED = "retrieval.acl_dropped"
    FILTER_DROPPED = "retrieval.filter_dropped"
