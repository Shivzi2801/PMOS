"""
Tenant resolver.

Turns an inbound request's credentials into a fully-populated TenantContext.
This is the front door of every authenticated request: the API slice hands the
resolver whatever the client presented (an API key, a session token, or a
system principal) and gets back a verified context — or a precise error.

Resolution paths:
  - API key      -> validate via APIKeyManager -> tenant/workspace/roles
  - session      -> validate via SessionManager -> tenant/workspace/user/roles
  - system       -> internal principal (orchestration jobs) with SYSTEM role

The resolver records `tenant_resolution_latency` so slow lookups are visible,
and never trusts client-supplied tenant_ids: the tenant is always derived from
the credential, which is what prevents tenant spoofing.
"""

from __future__ import annotations

from time import perf_counter
from typing import Optional

from .errors import (
    AuthenticationError,
    InactiveEntityError,
    UnknownTenantError,
)
from .role import DefaultRoles
from .tenant_context import TenantContext


class TenantResolver:
    def __init__(self, *, tenant_store, workspace_store, api_key_manager,
                 session_manager, metrics=None, audit=None):
        self._tenants = tenant_store
        self._workspaces = workspace_store
        self._api_keys = api_key_manager
        self._sessions = session_manager
        self._metrics = metrics
        self._audit = audit

    # --- public entry points ------------------------------------------------
    def resolve_from_api_key(self, raw_key: str,
                             correlation_id: Optional[str] = None) -> TenantContext:
        start = perf_counter()
        try:
            record = self._api_keys.validate(raw_key)  # raises on invalid/revoked
            tenant = self._require_active_tenant(record.tenant_id)
            self._require_workspace_if_set(record.tenant_id, record.workspace_id)
            ctx = TenantContext(
                tenant_id=tenant.tenant_id,
                workspace_id=record.workspace_id,
                api_key_id=record.key_id,
                roles=list(record.roles),
                auth_method="api_key",
                correlation_id=correlation_id,
            )
            return ctx
        finally:
            self._record_latency(start)

    def resolve_from_session(self, session_token: str,
                             correlation_id: Optional[str] = None) -> TenantContext:
        start = perf_counter()
        try:
            session = self._sessions.validate(session_token)  # raises if expired
            tenant = self._require_active_tenant(session.tenant_id)
            self._require_workspace_if_set(session.tenant_id, session.workspace_id)
            ctx = TenantContext(
                tenant_id=tenant.tenant_id,
                workspace_id=session.workspace_id,
                user_id=session.user_id,
                session_id=session.session_id,
                roles=list(session.roles),
                auth_method="session",
                correlation_id=correlation_id,
            )
            return ctx
        finally:
            self._record_latency(start)

    def resolve_system(self, tenant_id: str,
                       workspace_id: Optional[str] = None,
                       correlation_id: Optional[str] = None) -> TenantContext:
        """Internal principal for orchestration-initiated background work."""
        start = perf_counter()
        try:
            tenant = self._require_active_tenant(tenant_id)
            self._require_workspace_if_set(tenant_id, workspace_id)
            return TenantContext(
                tenant_id=tenant.tenant_id,
                workspace_id=workspace_id,
                roles=[DefaultRoles.SYSTEM],
                auth_method="system",
                correlation_id=correlation_id,
            )
        finally:
            self._record_latency(start)

    # --- helpers ------------------------------------------------------------
    def _require_active_tenant(self, tenant_id: str):
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise UnknownTenantError("unknown tenant",
                                     detail={"tenant_id": tenant_id})
        if not tenant.is_active:
            raise InactiveEntityError("tenant is not active",
                                      detail={"tenant_id": tenant_id,
                                              "status": tenant.status.value})
        return tenant

    def _require_workspace_if_set(self, tenant_id: str,
                                  workspace_id: Optional[str]) -> None:
        if workspace_id is None:
            return
        ws = self._workspaces.get(workspace_id)
        # Workspace must exist AND belong to the same tenant (isolation).
        if ws is None or ws.tenant_id != tenant_id:
            from .errors import WorkspaceNotFoundError
            raise WorkspaceNotFoundError(
                "workspace not found in tenant",
                detail={"tenant_id": tenant_id, "workspace_id": workspace_id},
            )
        if not ws.is_active:
            raise InactiveEntityError("workspace is not active",
                                      detail={"workspace_id": workspace_id})

    def _record_latency(self, start: float) -> None:
        if self._metrics:
            self._metrics.observe_tenant_resolution(perf_counter() - start)
