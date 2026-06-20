"""
PMOS Observability & Monitoring — Generation Metrics (S2.6)

Typed facade over :class:`MetricsCollector` for the Generation slice (S1.8).
Records LLM latency, token counts, per-call cost, and success/error rates.
Token + cost values additionally flow into the platform-wide rollup metrics so
the cost/usage reports can aggregate without re-deriving per-model totals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .metrics_collector import MetricsCollector


@dataclass(frozen=True)
class GenerationOutcome:
    model: str
    tenant_id: str
    duration_ms: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float = 0.0
    success: bool = True

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class GenerationMetrics:
    M_LATENCY = "pmos.generation.latency_ms"
    M_PROMPT = "pmos.generation.prompt_tokens"
    M_COMPLETION = "pmos.generation.completion_tokens"
    M_COST = "pmos.generation.cost_usd"
    M_RUNS = "pmos.generation.runs_total"
    M_TOKENS_TOTAL = "pmos.tokens_total"
    M_COST_TOTAL = "pmos.cost_usd_total"

    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    def record(self, outcome: GenerationOutcome) -> None:
        base = {"model": outcome.model, "tenant": outcome.tenant_id}
        self._collector.observe(self.M_LATENCY, outcome.duration_ms, labels=base)
        self._collector.observe(
            self.M_PROMPT, float(outcome.prompt_tokens), labels=base
        )
        self._collector.observe(
            self.M_COMPLETION, float(outcome.completion_tokens), labels=base
        )
        self._collector.increment(self.M_COST, amount=outcome.cost_usd, labels=base)
        self._collector.increment(
            self.M_RUNS,
            labels={
                "model": outcome.model,
                "status": "success" if outcome.success else "error",
                "tenant": outcome.tenant_id,
            },
        )

        # Platform-wide rollups for reporting.
        self._collector.increment(
            self.M_TOKENS_TOTAL, amount=float(outcome.prompt_tokens),
            labels={"model": outcome.model, "kind": "prompt", "tenant": outcome.tenant_id},
        )
        self._collector.increment(
            self.M_TOKENS_TOTAL, amount=float(outcome.completion_tokens),
            labels={"model": outcome.model, "kind": "completion", "tenant": outcome.tenant_id},
        )
        self._collector.increment(
            self.M_COST_TOTAL, amount=outcome.cost_usd,
            labels={"model": outcome.model, "tenant": outcome.tenant_id},
        )


__all__ = ["GenerationMetrics", "GenerationOutcome"]
