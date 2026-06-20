"""Configuration service: the public façade for configuration management.

Orchestrates the validator (correctness), the registry (persistence/versioning)
and cross-cutting concerns (audit logging, metrics). It exposes the five
required capabilities — load, update, validate, version, rollback — and computes
the *effective* configuration by resolving the system -> tenant -> workspace
layering.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from .audit_service import AuditService
from .configuration_registry import ConfigurationRegistry
from .configuration_validator import ConfigurationValidator
from .errors import ConfigurationNotFoundError
from .metrics import CONFIG_CHANGES, get_metrics
from .models import AuditCategory, utcnow
from .models.configuration import (
    SystemConfiguration,
    TenantConfiguration,
    WorkspaceConfiguration,
)

_SYSTEM_KEY = "system"


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto ``base`` (overlay wins on conflict)."""
    result: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], Mapping)
            and isinstance(value, Mapping)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigurationService:
    """Manage system, tenant and workspace configuration documents."""

    def __init__(
        self,
        *,
        validator: Optional[ConfigurationValidator] = None,
        audit: Optional[AuditService] = None,
        system_registry: Optional[ConfigurationRegistry[SystemConfiguration]] = None,
        tenant_registry: Optional[ConfigurationRegistry[TenantConfiguration]] = None,
        workspace_registry: Optional[ConfigurationRegistry[WorkspaceConfiguration]] = None,
    ) -> None:
        self._validator = validator or ConfigurationValidator()
        self._audit = audit
        self._system = system_registry or ConfigurationRegistry[SystemConfiguration]()
        self._tenant = tenant_registry or ConfigurationRegistry[TenantConfiguration]()
        self._workspace = (
            workspace_registry or ConfigurationRegistry[WorkspaceConfiguration]()
        )

    # ------------------------------------------------------------------ #
    # System
    # ------------------------------------------------------------------ #
    def init_system(
        self, settings: Mapping[str, Any], *, actor_id: str = "system"
    ) -> SystemConfiguration:
        normalized = self._validator.validate(_SYSTEM_KEY, settings)
        cfg = SystemConfiguration(settings=dict(normalized), updated_by=actor_id)
        created = self._system.create(_SYSTEM_KEY, cfg)
        self._record_change("system", _SYSTEM_KEY, None, created, actor_id, "create")
        return created

    def get_system(self) -> SystemConfiguration:
        return self._system.get(_SYSTEM_KEY)

    # ------------------------------------------------------------------ #
    # Tenant
    # ------------------------------------------------------------------ #
    def create_tenant_config(
        self,
        tenant_id: str,
        settings: Mapping[str, Any],
        *,
        display_name: str = "",
        data_residency: str = "global",
        actor_id: str = "system",
    ) -> TenantConfiguration:
        normalized = self._validator.validate("tenant", settings)
        cfg = TenantConfiguration(
            tenant_id=tenant_id,
            display_name=display_name,
            data_residency=data_residency,
            settings=dict(normalized),
            updated_by=actor_id,
        )
        created = self._tenant.create(tenant_id, cfg)
        self._record_change("tenant", tenant_id, None, created, actor_id, "create")
        return created

    def load_tenant_config(self, tenant_id: str) -> TenantConfiguration:
        """Load capability — fetch a tenant configuration document."""
        return self._tenant.get(tenant_id)

    def update_tenant_config(
        self,
        tenant_id: str,
        settings: Mapping[str, Any],
        *,
        expected_version: int,
        actor_id: str = "system",
    ) -> TenantConfiguration:
        """Update capability with optimistic concurrency + validation + audit."""
        current = self._tenant.get(tenant_id)
        normalized = self._validator.validate("tenant", settings)
        updated = copy.deepcopy(current)
        updated.settings = dict(normalized)
        updated.updated_at = utcnow()
        updated.updated_by = actor_id
        saved = self._tenant.update(tenant_id, updated, expected_version=expected_version)
        self._record_change("tenant", tenant_id, current, saved, actor_id, "update")
        return saved

    def rollback_tenant_config(
        self, tenant_id: str, target_version: int, *, actor_id: str = "system"
    ) -> TenantConfiguration:
        """Rollback capability — restore a prior version as a new version."""
        before = self._tenant.get(tenant_id)
        restored = self._tenant.rollback(tenant_id, target_version)
        self._record_change(
            "tenant", tenant_id, before, restored, actor_id, "rollback",
            extra={"target_version": target_version},
        )
        return restored

    def tenant_config_history(self, tenant_id: str) -> list[TenantConfiguration]:
        """Version capability — full version history."""
        return self._tenant.history(tenant_id)

    # ------------------------------------------------------------------ #
    # Workspace
    # ------------------------------------------------------------------ #
    def create_workspace_config(
        self,
        workspace_id: str,
        tenant_id: str,
        settings: Mapping[str, Any],
        *,
        display_name: str = "",
        actor_id: str = "system",
    ) -> WorkspaceConfiguration:
        normalized = self._validator.validate("workspace", settings)
        cfg = WorkspaceConfiguration(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            display_name=display_name,
            settings=dict(normalized),
            updated_by=actor_id,
        )
        created = self._workspace.create(workspace_id, cfg)
        self._record_change("workspace", workspace_id, None, created, actor_id, "create")
        return created

    def load_workspace_config(self, workspace_id: str) -> WorkspaceConfiguration:
        return self._workspace.get(workspace_id)

    def update_workspace_config(
        self,
        workspace_id: str,
        settings: Mapping[str, Any],
        *,
        expected_version: int,
        actor_id: str = "system",
    ) -> WorkspaceConfiguration:
        current = self._workspace.get(workspace_id)
        normalized = self._validator.validate("workspace", settings)
        updated = copy.deepcopy(current)
        updated.settings = dict(normalized)
        updated.updated_at = utcnow()
        updated.updated_by = actor_id
        saved = self._workspace.update(
            workspace_id, updated, expected_version=expected_version
        )
        self._record_change("workspace", workspace_id, current, saved, actor_id, "update")
        return saved

    def rollback_workspace_config(
        self, workspace_id: str, target_version: int, *, actor_id: str = "system"
    ) -> WorkspaceConfiguration:
        before = self._workspace.get(workspace_id)
        restored = self._workspace.rollback(workspace_id, target_version)
        self._record_change(
            "workspace", workspace_id, before, restored, actor_id, "rollback",
            extra={"target_version": target_version},
        )
        return restored

    def workspace_config_history(
        self, workspace_id: str
    ) -> list[WorkspaceConfiguration]:
        return self._workspace.history(workspace_id)

    def delete_workspace_config(self, workspace_id: str, *, actor_id: str = "system") -> None:
        before = self._workspace.get_or_none(workspace_id)
        self._workspace.delete(workspace_id)
        if before is not None:
            self._record_change(
                "workspace", workspace_id, before, None, actor_id, "delete"
            )

    def delete_tenant_config(self, tenant_id: str, *, actor_id: str = "system") -> None:
        before = self._tenant.get_or_none(tenant_id)
        self._tenant.delete(tenant_id)
        if before is not None:
            self._record_change("tenant", tenant_id, before, None, actor_id, "delete")

    # ------------------------------------------------------------------ #
    # Effective configuration resolution
    # ------------------------------------------------------------------ #
    def effective_config(
        self, *, tenant_id: str, workspace_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Resolve the layered effective settings for a runtime context.

        Order (lowest precedence first): system -> tenant -> workspace.
        Missing layers are skipped gracefully so that a tenant without an
        explicit document still inherits system defaults.
        """
        system = self._system.get_or_none(_SYSTEM_KEY)
        merged: dict[str, Any] = dict(system.settings) if system else {}

        tenant = self._tenant.get_or_none(tenant_id)
        if tenant is not None:
            merged = _deep_merge(merged, tenant.settings)

        if workspace_id is not None:
            ws = self._workspace.get_or_none(workspace_id)
            if ws is not None:
                merged = _deep_merge(merged, ws.settings)
        return merged

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _record_change(
        self,
        scope: str,
        key: str,
        before: Any,
        after: Any,
        actor_id: str,
        operation: str,
        *,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        get_metrics().increment(CONFIG_CHANGES, scope=scope, operation=operation)
        if self._audit is None:
            return
        self._audit.record(
            category=AuditCategory.CONFIGURATION,
            action=f"configuration.{operation}",
            actor_id=actor_id,
            target_type=f"{scope}_configuration",
            target_id=key,
            before=before.to_dict() if hasattr(before, "to_dict") and before else None,
            after=after.to_dict() if hasattr(after, "to_dict") and after else None,
            metadata=dict(extra or {}),
        )
