"""
PMOS Observability & Monitoring — Metrics Snapshot (S2.6)

Immutable, point-in-time views of collected metric state. Snapshots are what
exporters serialize, what the alert engine evaluates against, and what
dashboard projections summarize. Decoupling the *snapshot* from the live
collector means evaluation/serialization never races with concurrent
recording.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

from .metrics_registry import MetricKind, MetricUnit


# A label set is normalized to a sorted tuple of (key, value) pairs so it can
# be used as a dict key and compared deterministically.
LabelSet = Tuple[Tuple[str, str], ...]


def normalize_labels(labels: Mapping[str, str]) -> LabelSet:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def labels_to_dict(labels: LabelSet) -> Dict[str, str]:
    return {k: v for k, v in labels}


@dataclass(frozen=True)
class HistogramState:
    """Aggregated histogram statistics for a single label set."""

    bucket_bounds: Tuple[float, ...]
    bucket_counts: Tuple[int, ...]  # parallel to bucket_bounds; last is +Inf overflow
    count: int
    sum: float
    min: Optional[float]
    max: Optional[float]

    @property
    def mean(self) -> float:
        return self.sum / self.count if self.count else 0.0

    def quantile(self, q: float) -> float:
        """Estimate the q-quantile (0..1) using linear interpolation within
        the matching bucket. This is an approximation appropriate for SLO
        monitoring, not exact percentile computation.
        """
        if not 0.0 <= q <= 1.0:
            raise ValueError("quantile must be in [0, 1]")
        if self.count == 0:
            return 0.0
        target = q * self.count
        cumulative = 0
        lower_bound = 0.0
        for i, upper in enumerate(self.bucket_bounds):
            bucket_count = self.bucket_counts[i]
            if cumulative + bucket_count >= target:
                if bucket_count == 0:
                    return upper
                # Linear interpolation within [lower_bound, upper].
                position = (target - cumulative) / bucket_count
                if math.isinf(upper):
                    return lower_bound
                return lower_bound + position * (upper - lower_bound)
            cumulative += bucket_count
            lower_bound = upper if not math.isinf(upper) else lower_bound
        return self.max if self.max is not None else lower_bound


@dataclass(frozen=True)
class MetricSample:
    """One metric's full state for one label set."""

    name: str
    kind: MetricKind
    unit: MetricUnit
    labels: LabelSet
    # Populated according to kind:
    value: Optional[float] = None              # COUNTER / GAUGE
    histogram: Optional[HistogramState] = None  # HISTOGRAM

    @property
    def label_dict(self) -> Dict[str, str]:
        return labels_to_dict(self.labels)


@dataclass(frozen=True)
class MetricsSnapshot:
    """An immutable collection of all metric samples at a moment in time."""

    captured_at: float  # epoch seconds
    samples: Tuple[MetricSample, ...] = field(default_factory=tuple)

    def by_name(self, name: str) -> Tuple[MetricSample, ...]:
        return tuple(s for s in self.samples if s.name == name)

    def find(
        self, name: str, labels: Optional[Mapping[str, str]] = None
    ) -> Optional[MetricSample]:
        target = normalize_labels(labels) if labels is not None else None
        for s in self.samples:
            if s.name != name:
                continue
            if target is None or s.labels == target:
                return s
        return None

    def names(self) -> Tuple[str, ...]:
        seen: dict[str, None] = {}
        for s in self.samples:
            seen.setdefault(s.name, None)
        return tuple(seen.keys())

    def counter_total(self, name: str) -> float:
        """Sum a counter across every label set (useful for SLA math)."""
        return sum(
            s.value or 0.0
            for s in self.samples
            if s.name == name and s.kind is MetricKind.COUNTER
        )


__all__ = [
    "LabelSet",
    "normalize_labels",
    "labels_to_dict",
    "HistogramState",
    "MetricSample",
    "MetricsSnapshot",
]
