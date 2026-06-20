"""Quota service: definition, usage tracking and enforcement.

Quotas constrain measurable resource usage (requests, ingested bytes, stored
bytes, retrievals, generations) per tenant/workspace over a rolling window.

The service maintains a usage counter per quota key. The core primitive is
:meth:`check_and_consume`, which atomically verifies headroom and, if allowed,
records the consumption. Enforcement is window-aware: when a window elapses the
counter resets (except for cumulative ``TOTAL`` quotas such as storage).
"""
from __future__ import annotations

import threading
from typing import Optional

from .audit_service import AuditService
from .errors import (
    QuotaError,
    QuotaExceededError,
    QuotaNotFoundError,
)
from .metrics import QUOTA_CHECKS, QUOTA_VIOLATIONS, get_metrics
from .models import (
    AuditCategory,
    Quota,
    QuotaPeriod,
    QuotaScope,
    UsageLimit,
    utcnow,
)


class QuotaService:
    """Manage quota definitions and enforce usage limits."""

    def __init__(self, *, audit: Optional[AuditService] = None) -> None:
        self._lock = threading.RLock()
        self._quotas: dict[str, Quota] = {}  # keyed by Quota.key
        self._usage: dict[str, UsageLimit] = {}  # keyed by Quota.key
        self._audit = audit

    # -- definition -------------------------------------------------------- #
    def define_quota(
        self,
        *,
        tenant_id: str,
        scope: QuotaScope,
        period: QuotaPeriod,
        limit: float,
        workspace_id: Optional[str] = None,
        unit: str = "count",
        enforced: bool = True,
        actor_id: str = "system",
    ) -> Quota:
        if limit < 0:
            raise QuotaError("quota limit must be non-negative", details={"limit": limit})
        quota = Quota(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            scope=scope,
            period=period,
            limit=limit,
            unit=unit,
            enforced=enforced,
        )
        with self._lock:
            self._quotas[quota.key] = quota
            self._usage.setdefault(quota.key, UsageLimit(quota_key=quota.key))
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.CONFIGURATION,
                action="quota.define",
                actor_id=actor_id,
                target_type="quota",
                target_id=quota.key,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                after=quota.to_dict(),
            )
        return quota

    def get_quota(self, key: str) -> Quota:
        with self._lock:
            quota = self._quotas.get(key)
            if quota is None:
                raise QuotaNotFoundError(f"quota '{key}' not found", details={"key": key})
            return quota

    def list_quotas(
        self, *, tenant_id: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> list[Quota]:
        with self._lock:
            quotas = list(self._quotas.values())
        return [
            q
            for q in quotas
            if (tenant_id is None or q.tenant_id == tenant_id)
            and (workspace_id is None or q.workspace_id == workspace_id)
        ]

    def _resolve_key(
        self,
        tenant_id: str,
        scope: QuotaScope,
        period: QuotaPeriod,
        workspace_id: Optional[str],
    ) -> str:
        ws = workspace_id or "*"
        return f"{tenant_id}:{ws}:{scope.value}:{period.value}"

    # -- usage tracking ---------------------------------------------------- #
    def _usage_for(self, quota: Quota, *, now=None) -> UsageLimit:
        now = now or utcnow()
        usage = self._usage.get(quota.key)
        if usage is None:
            usage = UsageLimit(quota_key=quota.key, window_start=now)
            self._usage[quota.key] = usage
        if usage.window_expired(quota.period_seconds(), now=now):
            usage.current = 0.0
            usage.window_start = now
        return usage

    def current_usage(
        self,
        *,
        tenant_id: str,
        scope: QuotaScope,
        period: QuotaPeriod,
        workspace_id: Optional[str] = None,
    ) -> float:
        key = self._resolve_key(tenant_id, scope, period, workspace_id)
        with self._lock:
            quota = self._quotas.get(key)
            if quota is None:
                raise QuotaNotFoundError(f"quota '{key}' not found", details={"key": key})
            return self._usage_for(quota).current

    def check_and_consume(
        self,
        *,
        tenant_id: str,
        scope: QuotaScope,
        period: QuotaPeriod,
        amount: float = 1.0,
        workspace_id: Optional[str] = None,
        actor_id: str = "system",
    ) -> UsageLimit:
        """Atomically enforce and record usage.

        If consuming ``amount`` would exceed an enforced quota, raises
        :class:`QuotaExceededError` and records nothing. Non-enforced quotas are
        tracked but never block (shadow mode). Missing quotas are treated as
        unlimited (returns a transient usage record).
        """
        if amount < 0:
            raise QuotaError("consume amount must be non-negative", details={"amount": amount})
        key = self._resolve_key(tenant_id, scope, period, workspace_id)
        now = utcnow()
        with self._lock:
            quota = self._quotas.get(key)
            get_metrics().increment(QUOTA_CHECKS, scope=scope.value, period=period.value)
            if quota is None:
                # No quota defined => unlimited. Track ad-hoc for observability.
                usage = self._usage.setdefault(key, UsageLimit(quota_key=key))
                usage.current += amount
                usage.updated_at = now
                return usage

            usage = self._usage_for(quota, now=now)
            projected = usage.current + amount
            if quota.enforced and projected > quota.limit:
                get_metrics().increment(
                    QUOTA_VIOLATIONS, scope=scope.value, period=period.value
                )
                if self._audit is not None:
                    self._audit.record(
                        category=AuditCategory.SECURITY,
                        action="quota.exceeded",
                        actor_id=actor_id,
                        target_type="quota",
                        target_id=key,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        outcome="denied",
                        metadata={
                            "limit": quota.limit,
                            "current": usage.current,
                            "requested": amount,
                        },
                    )
                raise QuotaExceededError(
                    f"quota '{key}' exceeded",
                    quota_key=key,
                    limit=quota.limit,
                    current=usage.current,
                    requested=amount,
                )
            usage.current = projected
            usage.updated_at = now
            return usage

    def reset_usage(
        self,
        *,
        tenant_id: str,
        scope: QuotaScope,
        period: QuotaPeriod,
        workspace_id: Optional[str] = None,
        actor_id: str = "system",
    ) -> None:
        key = self._resolve_key(tenant_id, scope, period, workspace_id)
        with self._lock:
            usage = self._usage.get(key)
            if usage is not None:
                usage.current = 0.0
                usage.window_start = utcnow()
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.ADMIN_ACTION,
                action="quota.reset",
                actor_id=actor_id,
                target_type="quota",
                target_id=key,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )

    def usage_report(
        self, *, tenant_id: str, workspace_id: Optional[str] = None
    ) -> list[dict]:
        """Summarize usage vs limit for all quotas in scope."""
        report = []
        for quota in self.list_quotas(tenant_id=tenant_id, workspace_id=workspace_id):
            with self._lock:
                usage = self._usage_for(quota)
                report.append(
                    {
                        "key": quota.key,
                        "scope": quota.scope.value,
                        "period": quota.period.value,
                        "limit": quota.limit,
                        "current": usage.current,
                        "unit": quota.unit,
                        "enforced": quota.enforced,
                        "utilization": (usage.current / quota.limit) if quota.limit else 0.0,
                    }
                )
        return report
