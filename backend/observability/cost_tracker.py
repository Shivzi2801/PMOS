"""
PMOS Observability & Monitoring — Cost Tracker (S2.6)

Translates token usage into estimated monetary cost using a pluggable pricing
table, and maintains an exact per-(tenant, model) cost ledger. Pricing is
expressed per 1,000 tokens with separate prompt/completion rates, matching how
LLM vendors publish prices.

The pricing table is injectable so deployments can override defaults or load
prices from configuration without code changes.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from .errors import CostTrackingError


@dataclass(frozen=True)
class ModelPricing:
    """Per-1K-token pricing for a model."""

    model: str
    prompt_per_1k_usd: float
    completion_per_1k_usd: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            (prompt_tokens / 1000.0) * self.prompt_per_1k_usd
            + (completion_tokens / 1000.0) * self.completion_per_1k_usd
        )


# Representative default pricing. Real values are deployment configuration;
# these exist so the subsystem is functional out of the box and tests are
# deterministic.
DEFAULT_PRICING: Tuple[ModelPricing, ...] = (
    ModelPricing("claude-opus-4", 0.015, 0.075),
    ModelPricing("claude-sonnet-4", 0.003, 0.015),
    ModelPricing("claude-haiku-4", 0.00080, 0.0040),
    ModelPricing("gpt-4o", 0.0050, 0.0150),
    ModelPricing("gpt-4o-mini", 0.00015, 0.00060),
    ModelPricing("text-embedding-3-large", 0.00013, 0.0),
)


class PricingTable:
    """A lookup of :class:`ModelPricing` with an optional default fallback."""

    def __init__(
        self,
        pricing: Optional[Mapping[str, ModelPricing]] = None,
        *,
        default: Optional[ModelPricing] = None,
    ) -> None:
        self._pricing: Dict[str, ModelPricing] = dict(pricing or {})
        self._default = default

    @classmethod
    def with_defaults(cls) -> "PricingTable":
        return cls({p.model: p for p in DEFAULT_PRICING})

    def set(self, pricing: ModelPricing) -> None:
        self._pricing[pricing.model] = pricing

    def get(self, model: str) -> ModelPricing:
        pricing = self._pricing.get(model, self._default)
        if pricing is None:
            raise CostTrackingError(
                f"No pricing configured for model '{model}'",
                details={"model": model},
            )
        return pricing


@dataclass(frozen=True)
class CostLedgerEntry:
    tenant_id: str
    model: str
    cost_usd: float


class CostTracker:
    """Computes and accumulates estimated spend per (tenant, model)."""

    def __init__(self, pricing: Optional[PricingTable] = None) -> None:
        self._pricing = pricing or PricingTable.with_defaults()
        self._lock = threading.Lock()
        self._ledger: Dict[Tuple[str, str], float] = defaultdict(float)

    def estimate(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Return the estimated cost without recording it."""
        return self._pricing.get(model).cost(prompt_tokens, completion_tokens)

    def record(
        self,
        *,
        tenant_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Compute, accumulate, and return the cost for this usage."""
        cost = self.estimate(model, prompt_tokens, completion_tokens)
        with self._lock:
            self._ledger[(tenant_id, model)] += cost
        return cost

    def entries(self) -> Tuple[CostLedgerEntry, ...]:
        with self._lock:
            return tuple(
                CostLedgerEntry(tenant_id=t, model=m, cost_usd=c)
                for (t, m), c in self._ledger.items()
            )

    def for_tenant(self, tenant_id: str) -> Tuple[CostLedgerEntry, ...]:
        return tuple(e for e in self.entries() if e.tenant_id == tenant_id)

    def total_cost(self) -> float:
        with self._lock:
            return sum(self._ledger.values())

    def tenant_total(self, tenant_id: str) -> float:
        return sum(e.cost_usd for e in self.for_tenant(tenant_id))

    def reset(self) -> None:
        with self._lock:
            self._ledger.clear()


__all__ = [
    "ModelPricing",
    "PricingTable",
    "CostTracker",
    "CostLedgerEntry",
    "DEFAULT_PRICING",
]
