"""
PMOS Observability & Monitoring — Usage Report (S2.6)

Builds operational usage, cost, and tenant-summary reports by combining the
exact ledgers (:class:`TokenTracker`, :class:`CostTracker`) with the metrics
snapshot. Reports are immutable value objects suitable for serialization to an
admin API (S2.5) or scheduled export.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .cost_tracker import CostTracker
from .metrics_snapshot import MetricsSnapshot
from .token_tracker import TokenTracker


@dataclass(frozen=True)
class ModelUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class TenantUsageSummary:
    tenant_id: str
    total_tokens: int
    total_cost_usd: float
    by_model: Tuple[ModelUsage, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "by_model": [
                {
                    "model": m.model,
                    "prompt_tokens": m.prompt_tokens,
                    "completion_tokens": m.completion_tokens,
                    "total_tokens": m.total_tokens,
                    "cost_usd": round(m.cost_usd, 6),
                }
                for m in self.by_model
            ],
        }


@dataclass(frozen=True)
class UsageReport:
    generated_at: float
    period_label: str
    total_tokens: int
    total_cost_usd: float
    tenants: Tuple[TenantUsageSummary, ...] = field(default_factory=tuple)

    def tenant(self, tenant_id: str) -> Optional[TenantUsageSummary]:
        for t in self.tenants:
            if t.tenant_id == tenant_id:
                return t
        return None

    def to_dict(self) -> Dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "period_label": self.period_label,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "tenants": [t.to_dict() for t in self.tenants],
        }


class UsageReportBuilder:
    """Assembles :class:`UsageReport` objects from the trackers."""

    def __init__(
        self,
        token_tracker: TokenTracker,
        cost_tracker: CostTracker,
        *,
        clock=time.time,
    ) -> None:
        self._tokens = token_tracker
        self._cost = cost_tracker
        self._clock = clock

    def build(self, *, period_label: str = "lifetime") -> UsageReport:
        # Merge token + cost ledgers keyed by (tenant, model).
        token_entries = self._tokens.entries()
        cost_entries = {(e.tenant_id, e.model): e.cost_usd for e in self._cost.entries()}

        # Group per tenant.
        per_tenant: Dict[str, List[ModelUsage]] = {}
        for te in token_entries:
            cost = cost_entries.get((te.tenant_id, te.model), 0.0)
            per_tenant.setdefault(te.tenant_id, []).append(
                ModelUsage(
                    model=te.model,
                    prompt_tokens=te.prompt_tokens,
                    completion_tokens=te.completion_tokens,
                    cost_usd=cost,
                )
            )

        # Include cost-only entries (e.g. embeddings) that lack token ledger rows.
        token_keys = {(te.tenant_id, te.model) for te in token_entries}
        for (tenant, model), cost in cost_entries.items():
            if (tenant, model) not in token_keys:
                per_tenant.setdefault(tenant, []).append(
                    ModelUsage(model=model, prompt_tokens=0, completion_tokens=0, cost_usd=cost)
                )

        tenants: List[TenantUsageSummary] = []
        grand_tokens = 0
        grand_cost = 0.0
        for tenant_id, models in sorted(per_tenant.items()):
            models_sorted = tuple(sorted(models, key=lambda m: m.model))
            t_tokens = sum(m.total_tokens for m in models_sorted)
            t_cost = sum(m.cost_usd for m in models_sorted)
            grand_tokens += t_tokens
            grand_cost += t_cost
            tenants.append(
                TenantUsageSummary(
                    tenant_id=tenant_id,
                    total_tokens=t_tokens,
                    total_cost_usd=t_cost,
                    by_model=models_sorted,
                )
            )

        return UsageReport(
            generated_at=self._clock(),
            period_label=period_label,
            total_tokens=grand_tokens,
            total_cost_usd=grand_cost,
            tenants=tuple(tenants),
        )


__all__ = [
    "ModelUsage",
    "TenantUsageSummary",
    "UsageReport",
    "UsageReportBuilder",
]
