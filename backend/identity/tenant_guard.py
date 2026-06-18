"""
Tenant guard.

The enforcement point that sits between a resolved TenantContext and any
protected operation. It answers two questions on every call:

  1. ISOLATION: does this resource belong to the caller's tenant (and workspace
     when scoped)? Any mismatch is a hard CrossTenantAccessError.
  2. AUTHORIZATION: do the caller's roles grant the required permission? If not,
     PermissionDeniedError.

Centralising both checks here means downstream slices (retrieval, indexing,
orchestration, ...) get a single, audited, testable gate instead of
reimplementing tenant checks ad hoc. Every decision — allow or deny — is written
to the access audit.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .errors import (
    CrossTenantAccessError,
    PermissionDeniedError,
)
from .tenant_context import TenantContext, current


class TenantGuard:
    def __init__(self, rbac_engine, audit=None, metrics=None):
        self._rbac = rbac_engine
        self._audit = audit
        self._metrics = metrics

    # --- authorization ------------------------------------------------------
    def require_permission(self, permission: str,
                           ctx: Optional[TenantContext] = None) -> None:
        ctx = ctx or current()
        allowed = self._rbac.has_permission(ctx.roles, permission, ctx.tenant_id)
        self._audit_permission(ctx, permission, allowed)
        if not allowed:
            if self._metrics:
                self._metrics.incr_permission_denial()
            raise PermissionDeniedError(
                "permission denied",
                detail={"permission": permission, "roles": list(ctx.roles),
                        "tenant_id": ctx.tenant_id},
            )

    def require_any(self, permissions: Iterable[str],
                    ctx: Optional[TenantContext] = None) -> None:
        ctx = ctx or current()
        perms = list(permissions)
        allowed = self._rbac.has_any(ctx.roles, perms, ctx.tenant_id)
        self._audit_permission(ctx, "|".join(perms), allowed)
        if not allowed:
            if self._metrics:
                self._metrics.incr_permission_denial()
            raise PermissionDeniedError("permission denied",
                                        detail={"any_of": perms})

    def has_permission(self, permission: str,
                       ctx: Optional[TenantContext] = None) -> bool:
        ctx = ctx or current()
        return self._rbac.has_permission(ctx.roles, permission, ctx.tenant_id)

    # --- isolation ----------------------------------------------------------
    def require_same_tenant(self, resource_tenant_id: str,
                            ctx: Optional[TenantContext] = None) -> None:
        ctx = ctx or current()
        if resource_tenant_id != ctx.tenant_id:
            if self._audit:
                self._audit.record(
                    action="tenant_access", ctx=ctx, allowed=False,
                    detail={"resource_tenant_id": resource_tenant_id,
                            "reason": "tenant_mismatch"},
                )
            raise CrossTenantAccessError(
                "cross-tenant access denied",
                detail={"caller_tenant": ctx.tenant_id,
                        "resource_tenant": resource_tenant_id},
            )

    def require_same_workspace(self, resource_workspace_id: Optional[str],
                               ctx: Optional[TenantContext] = None) -> None:
        """Workspace-level scoping (only enforced when the caller is workspace-scoped)."""
        ctx = ctx or current()
        if ctx.workspace_id is None:
            return  # tenant-wide principal: workspace scoping not applicable
        if resource_workspace_id is not None and \
                resource_workspace_id != ctx.workspace_id:
            if self._audit:
                self._audit.record(
                    action="workspace_access", ctx=ctx, allowed=False,
                    detail={"resource_workspace_id": resource_workspace_id},
                )
            raise CrossTenantAccessError(
                "cross-workspace access denied",
                detail={"caller_workspace": ctx.workspace_id,
                        "resource_workspace": resource_workspace_id},
            )

    def guard_resource(self, resource: Any,
                       ctx: Optional[TenantContext] = None) -> None:
        """
        Convenience: enforce tenant (and workspace if present) against any object
        exposing `.tenant_id` / `.workspace_id` attributes or dict keys.
        """
        ctx = ctx or current()
        rt = self._attr(resource, "tenant_id")
        if rt is not None:
            self.require_same_tenant(rt, ctx)
        rw = self._attr(resource, "workspace_id")
        if rw is not None:
            self.require_same_workspace(rw, ctx)

    # --- helpers ------------------------------------------------------------
    @staticmethod
    def _attr(obj: Any, name: str):
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    def _audit_permission(self, ctx, permission, allowed):
        if self._audit:
            self._audit.record(action="permission_check", ctx=ctx,
                               allowed=allowed,
                               detail={"permission": permission})
