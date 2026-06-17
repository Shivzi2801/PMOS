"""Metrics for the Context Assembly layer (S1.7).

A lightweight, dependency-free counters/gauges collector. The assembler records
observability data here without committing to any metrics backend; callers can
export :meth:`AssemblyMetrics.snapshot` to their telemetry system of choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class AssemblyMetrics:
    """Mutable collector of context-assembly observability data."""

    input_chunks: int = 0
    duplicate_chunks_removed: int = 0
    acl_filtered_chunks: int = 0
    budget_dropped_chunks: int = 0
    selected_chunks: int = 0
    used_context_tokens: int = 0
    available_context_tokens: int = 0
    estimated_prompt_tokens: int = 0
    _timers: Dict[str, float] = field(default_factory=dict)

    def record_timer(self, name: str, seconds: float) -> None:
        self._timers[name] = self._timers.get(name, 0.0) + seconds

    @property
    def budget_utilization(self) -> float:
        """Fraction of the available context budget that was consumed."""
        if self.available_context_tokens <= 0:
            return 0.0
        return self.used_context_tokens / self.available_context_tokens

    def snapshot(self) -> Dict[str, float]:
        """Return a flat dict suitable for export to a telemetry backend."""
        data: Dict[str, float] = {
            "input_chunks": float(self.input_chunks),
            "duplicate_chunks_removed": float(self.duplicate_chunks_removed),
            "acl_filtered_chunks": float(self.acl_filtered_chunks),
            "budget_dropped_chunks": float(self.budget_dropped_chunks),
            "selected_chunks": float(self.selected_chunks),
            "used_context_tokens": float(self.used_context_tokens),
            "available_context_tokens": float(self.available_context_tokens),
            "estimated_prompt_tokens": float(self.estimated_prompt_tokens),
            "budget_utilization": self.budget_utilization,
        }
        for name, value in self._timers.items():
            data[f"timer.{name}"] = value
        return data
