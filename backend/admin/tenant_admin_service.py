"""Tenant administration: lifecycle and configuration management.

A tenant is the top-level isolation boundary in PMOS. This service owns the
tenant lifecycle (pending -> active -> suspended -> deleted), coordinates the
creation/teardown of a tenant's configuration document, and emits audit events
for every transition. Illegal transitions are rejected with
:class:`TenantStateError`.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .audit_service import AuditService
from .configuration_service import ConfigurationService
from .errors import TenantError, TenantNotFoundError, TenantStateError
from .metrics import TENANT_ACTIVITY, get_metrics
from .models import AuditCategory, TenantState, new_id, utcnow

# Allowed lifecycle transitions.
_TRANSITIONS: dict[TenantState, set[TenantState]] = {
    TenantState.PENDING: {TenantState.ACTIVE, TenantState.DELETED},
    TenantState.ACTIVE: {TenantState.SUSPENDED, TenantState.DELETED},
    TenantState.SUSPENDED: {TenantState.ACTIVE, TenantState.DELETED},
    TenantState.DELETED: set(),
}


@dataclass
class Tenant:
    """Lightweight tenant record (identity + lifecycle state).

    Configuration lives in the configuration service; this record holds only
    administrative identity and state so the two concerns evolve independently.
    """

    tenant_id: str
    name: str
    state: TenantState = TenantState.PENDING
    created_at: Any = field(default_factory=utcnow)
    updated_at: Any = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }


class TenantAdminService:
    """Create, mutate and tear down tenants."""

    def __init__(
        self,
        *,
        configuration: Optional[ConfigurationService] = None,
        audit: Optional[AuditService] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._tenants: dict[str, Tenant] = {}
        self._config = configuration
        self._audit = audit

    def create_tenant(
        self,
        name: str,
        *,
        tenant_id: Optional[str] = None,
        config_settings: Optional[Mapping[str, Any]] = None,
        data_residency: str = "global",
        activate: bool = True,
        actor_id: str = "system",
    ) -> Tenant:
        if not name or not name.strip():
            raise TenantError("tenant name must be non-empty")
        tid = tenant_id or new_id("tenant")
        with self._lock:
            if tid in self._tenants:
                raise TenantError(
                    f"tenant '{tid}' already exists", details={"tenant_id": tid}
                )
            tenant = Tenant(
                tenant_id=tid,
                name=name,
                state=TenantState.ACTIVE if activate else TenantState.PENDING,
            )
            self._tenants[tid] = tenant

        if self._config is not None:
            self._config.create_tenant_config(
                tid,
                dict(config_settings or {}),
                display_name=name,
                data_residency=data_residency,
                actor_id=actor_id,
            )
        self._activity("tenant.create", tenant, actor_id)
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant:
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if tenant is None:
                raise TenantNotFoundError(
                    f"tenant '{tenant_id}' not found", details={"tenant_id": tenant_id}
                )
            return tenant

    def list_tenants(self, *, state: Optional[TenantState] = None) -> list[Tenant]:
        with self._lock:
            tenants = list(self._tenants.values())
        return [t for t in tenants if state is None or t.state == state]

    def update_tenant(
        self,
        tenant_id: str,
        *,
        name: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        actor_id: str = "system",
    ) -> Tenant:
        with self._lock:
            tenant = self.get_tenant(tenant_id)
            if tenant.state == TenantState.DELETED:
                raise TenantStateError(
                    "cannot update a deleted tenant", details={"tenant_id": tenant_id}
                )
            if name is not None:
                tenant.name = name
            if metadata is not None:
                tenant.metadata.update(metadata)
            tenant.updated_at = utcnow()
        self._activity("tenant.update", tenant, actor_id)
        return tenant

    def suspend_tenant(self, tenant_id: str, *, actor_id: str = "system") -> Tenant:
        return self._transition(tenant_id, TenantState.SUSPENDED, "tenant.suspend", actor_id)

    def reactivate_tenant(self, tenant_id: str, *, actor_id: str = "system") -> Tenant:
        return self._transition(tenant_id, TenantState.ACTIVE, "tenant.reactivate", actor_id)

    def activate_tenant(self, tenant_id: str, *, actor_id: str = "system") -> Tenant:
        return self._transition(tenant_id, TenantState.ACTIVE, "tenant.activate", actor_id)

    def delete_tenant(self, tenant_id: str, *, actor_id: str = "system") -> Tenant:
        """Soft-delete: transition to DELETED and tear down configuration.

        We never hard-delete here — the audit trail and (optionally) the config
        history are retained for compliance. A separate, governed purge job may
        physically remove data once retention windows elapse.
        """
        tenant = self._transition(tenant_id, TenantState.DELETED, "tenant.delete", actor_id)
        if self._config is not None:
            self._config.delete_tenant_config(tenant_id, actor_id=actor_id)
        return tenant

    def manage_configuration(
        self,
        tenant_id: str,
        settings: Mapping[str, Any],
        *,
        expected_version: int,
        actor_id: str = "system",
    ):
        """Convenience pass-through to update a tenant's configuration."""
        if self._config is None:
            raise TenantError("no configuration service is wired into tenant admin")
        self.get_tenant(tenant_id)  # ensure tenant exists
        return self._config.update_tenant_config(
            tenant_id, settings, expected_version=expected_version, actor_id=actor_id
        )

    # internals ------------------------------------------------------------ #
    def _transition(
        self, tenant_id: str, target: TenantState, action: str, actor_id: str
    ) -> Tenant:
        with self._lock:
            tenant = self.get_tenant(tenant_id)
            if target == tenant.state:
                return tenant  # idempotent no-op
            allowed = _TRANSITIONS[tenant.state]
            if target not in allowed:
                raise TenantStateError(
                    f"illegal transition {tenant.state.value} -> {target.value}",
                    details={
                        "tenant_id": tenant_id,
                        "from": tenant.state.value,
                        "to": target.value,
                    },
                )
            before = tenant.state
            tenant.state = target
            tenant.updated_at = utcnow()
        self._activity(action, tenant, actor_id, extra={"from": before.value})
        return tenant

    def _activity(
        self,
        action: str,
        tenant: Tenant,
        actor_id: str,
        *,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        get_metrics().increment(TENANT_ACTIVITY, action=action.split(".")[-1])
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.TENANT,
                action=action,
                actor_id=actor_id,
                target_type="tenant",
                target_id=tenant.tenant_id,
                tenant_id=tenant.tenant_id,
                after=tenant.to_dict(),
                metadata=dict(extra or {}),
            )
