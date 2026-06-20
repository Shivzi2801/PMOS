"""Workspace administration: lifecycle, settings and governance hooks.

A workspace is a sub-division of a tenant. This service manages workspace
lifecycle (active -> archived -> deleted), workspace settings (delegated to the
configuration service), and provides a governance hook so workspace creation /
mutation can be validated against registered governance rules.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .audit_service import AuditService
from .configuration_service import ConfigurationService
from .errors import (
    TenantNotFoundError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceStateError,
)
from .governance_service import GovernanceService
from .metrics import WORKSPACE_ACTIVITY, get_metrics
from .models import AuditCategory, WorkspaceState, new_id, utcnow
from .tenant_admin_service import TenantAdminService

_TRANSITIONS: dict[WorkspaceState, set[WorkspaceState]] = {
    WorkspaceState.ACTIVE: {WorkspaceState.ARCHIVED, WorkspaceState.DELETED},
    WorkspaceState.ARCHIVED: {WorkspaceState.ACTIVE, WorkspaceState.DELETED},
    WorkspaceState.DELETED: set(),
}


@dataclass
class Workspace:
    workspace_id: str
    tenant_id: str
    name: str
    state: WorkspaceState = WorkspaceState.ACTIVE
    created_at: Any = field(default_factory=utcnow)
    updated_at: Any = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }


class WorkspaceAdminService:
    """Create, archive, configure and govern workspaces."""

    def __init__(
        self,
        *,
        tenant_admin: Optional[TenantAdminService] = None,
        configuration: Optional[ConfigurationService] = None,
        governance: Optional[GovernanceService] = None,
        audit: Optional[AuditService] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._workspaces: dict[str, Workspace] = {}
        self._tenant_admin = tenant_admin
        self._config = configuration
        self._governance = governance
        self._audit = audit

    def create_workspace(
        self,
        tenant_id: str,
        name: str,
        *,
        workspace_id: Optional[str] = None,
        config_settings: Optional[Mapping[str, Any]] = None,
        enforce_governance: bool = True,
        actor_id: str = "system",
    ) -> Workspace:
        if not name or not name.strip():
            raise WorkspaceError("workspace name must be non-empty")
        # Validate the owning tenant exists & is usable.
        if self._tenant_admin is not None:
            tenant = self._tenant_admin.get_tenant(tenant_id)  # raises if missing
            if tenant.state.value in ("suspended", "deleted"):
                raise WorkspaceError(
                    f"cannot create workspace under {tenant.state.value} tenant",
                    details={"tenant_id": tenant_id, "tenant_state": tenant.state.value},
                )

        wid = workspace_id or new_id("ws")
        settings = dict(config_settings or {})

        # Governance pre-validation of the intended workspace state.
        if enforce_governance and self._governance is not None:
            subject = {
                "type": "workspace",
                "tenant_id": tenant_id,
                "workspace_id": wid,
                "name": name,
                "settings": settings,
            }
            self._governance.enforce(subject)  # raises GovernanceViolationError

        with self._lock:
            if wid in self._workspaces:
                raise WorkspaceError(
                    f"workspace '{wid}' already exists", details={"workspace_id": wid}
                )
            ws = Workspace(workspace_id=wid, tenant_id=tenant_id, name=name)
            self._workspaces[wid] = ws

        if self._config is not None:
            self._config.create_workspace_config(
                wid, tenant_id, settings, display_name=name, actor_id=actor_id
            )
        self._activity("workspace.create", ws, actor_id)
        return ws

    def get_workspace(self, workspace_id: str) -> Workspace:
        with self._lock:
            ws = self._workspaces.get(workspace_id)
            if ws is None:
                raise WorkspaceNotFoundError(
                    f"workspace '{workspace_id}' not found",
                    details={"workspace_id": workspace_id},
                )
            return ws

    def list_workspaces(
        self,
        *,
        tenant_id: Optional[str] = None,
        state: Optional[WorkspaceState] = None,
    ) -> list[Workspace]:
        with self._lock:
            workspaces = list(self._workspaces.values())
        return [
            w
            for w in workspaces
            if (tenant_id is None or w.tenant_id == tenant_id)
            and (state is None or w.state == state)
        ]

    def update_settings(
        self,
        workspace_id: str,
        settings: Mapping[str, Any],
        *,
        expected_version: int,
        enforce_governance: bool = True,
        actor_id: str = "system",
    ):
        ws = self.get_workspace(workspace_id)
        if ws.state == WorkspaceState.DELETED:
            raise WorkspaceStateError(
                "cannot update a deleted workspace",
                details={"workspace_id": workspace_id},
            )
        if enforce_governance and self._governance is not None:
            self._governance.enforce(
                {
                    "type": "workspace",
                    "tenant_id": ws.tenant_id,
                    "workspace_id": workspace_id,
                    "name": ws.name,
                    "settings": dict(settings),
                }
            )
        if self._config is None:
            raise WorkspaceError("no configuration service wired into workspace admin")
        result = self._config.update_workspace_config(
            workspace_id, settings, expected_version=expected_version, actor_id=actor_id
        )
        with self._lock:
            ws.updated_at = utcnow()
        self._activity("workspace.update_settings", ws, actor_id)
        return result

    def archive_workspace(self, workspace_id: str, *, actor_id: str = "system") -> Workspace:
        return self._transition(
            workspace_id, WorkspaceState.ARCHIVED, "workspace.archive", actor_id
        )

    def unarchive_workspace(self, workspace_id: str, *, actor_id: str = "system") -> Workspace:
        return self._transition(
            workspace_id, WorkspaceState.ACTIVE, "workspace.unarchive", actor_id
        )

    def delete_workspace(self, workspace_id: str, *, actor_id: str = "system") -> Workspace:
        ws = self._transition(
            workspace_id, WorkspaceState.DELETED, "workspace.delete", actor_id
        )
        if self._config is not None:
            try:
                self._config.delete_workspace_config(workspace_id, actor_id=actor_id)
            except Exception:
                # config may not exist; deletion is best-effort and idempotent
                pass
        return ws

    # internals ------------------------------------------------------------ #
    def _transition(
        self, workspace_id: str, target: WorkspaceState, action: str, actor_id: str
    ) -> Workspace:
        with self._lock:
            ws = self.get_workspace(workspace_id)
            if ws.state == target:
                return ws
            if target not in _TRANSITIONS[ws.state]:
                raise WorkspaceStateError(
                    f"illegal transition {ws.state.value} -> {target.value}",
                    details={
                        "workspace_id": workspace_id,
                        "from": ws.state.value,
                        "to": target.value,
                    },
                )
            before = ws.state
            ws.state = target
            ws.updated_at = utcnow()
        self._activity(action, ws, actor_id, extra={"from": before.value})
        return ws

    def _activity(
        self,
        action: str,
        ws: Workspace,
        actor_id: str,
        *,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        get_metrics().increment(WORKSPACE_ACTIVITY, action=action.split(".")[-1])
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.WORKSPACE,
                action=action,
                actor_id=actor_id,
                target_type="workspace",
                target_id=ws.workspace_id,
                tenant_id=ws.tenant_id,
                workspace_id=ws.workspace_id,
                after=ws.to_dict(),
                metadata=dict(extra or {}),
            )
