"""Quota and usage-limit domain models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ._base import (
    DomainModel,
    QuotaPeriod,
    QuotaScope,
    new_id,
    utcnow,
)

_PERIOD_SECONDS: dict[QuotaPeriod, Optional[int]] = {
    QuotaPeriod.SECOND: 1,
    QuotaPeriod.MINUTE: 60,
    QuotaPeriod.HOUR: 3_600,
    QuotaPeriod.DAY: 86_400,
    QuotaPeriod.MONTH: 30 * 86_400,
    QuotaPeriod.TOTAL: None,  # never resets
}


@dataclass
class Quota(DomainModel):
    """A limit on a measurable resource for a tenant/workspace.

    ``limit`` is the maximum permitted usage within one ``period`` window.
    ``unit`` documents what is being counted (requests, bytes, tokens...).
    A non-enforcing quota (``enforced=False``) is tracked and reported on but
    never blocks an operation — useful for shadow-mode rollout of new limits.
    """

    scope: QuotaScope = QuotaScope.REQUEST
    id: str = field(default_factory=lambda: new_id("quota"))
    tenant_id: str = ""
    workspace_id: Optional[str] = None
    period: QuotaPeriod = QuotaPeriod.DAY
    limit: float = 0.0
    unit: str = "count"
    enforced: bool = True
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @property
    def key(self) -> str:
        """Stable identity used to address usage counters."""
        ws = self.workspace_id or "*"
        return f"{self.tenant_id}:{ws}:{self.scope.value}:{self.period.value}"

    def period_seconds(self) -> Optional[int]:
        return _PERIOD_SECONDS[self.period]


@dataclass
class UsageLimit(DomainModel):
    """A point-in-time usage observation against a quota.

    The quota service maintains these counters. ``window_start`` marks the
    beginning of the current measurement window; when the window elapses the
    counter is reset (except for :class:`QuotaPeriod.TOTAL`).
    """

    quota_key: str = ""
    id: str = field(default_factory=lambda: new_id("usage"))
    current: float = 0.0
    window_start: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def window_expired(self, period_seconds: Optional[int], *, now: Optional[datetime] = None) -> bool:
        if period_seconds is None:  # TOTAL never expires
            return False
        now = now or utcnow()
        return now >= self.window_start + timedelta(seconds=period_seconds)
