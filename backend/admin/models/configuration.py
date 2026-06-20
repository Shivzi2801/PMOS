"""Configuration domain models: tenant, workspace and system configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ._base import DomainModel, new_id, utcnow


@dataclass
class _VersionedConfig(DomainModel):
    """Common fields for any versioned configuration record.

    Versioning uses a monotonically increasing integer. Optimistic concurrency
    control is implemented by the registry: an update must present the version
    it expects to replace, otherwise a version conflict is raised.
    """

    version: int = 1
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    updated_by: Optional[str] = None
    checksum: Optional[str] = None


@dataclass
class TenantConfiguration(_VersionedConfig):
    """Per-tenant configuration root.

    Holds tenant-scoped settings that downstream slices read at request time:
    default models, regional routing, data residency, encryption keys (by
    reference), and arbitrary feature settings. Inherited by all workspaces of
    the tenant unless overridden at the workspace level.
    """

    id: str = field(default_factory=lambda: new_id("tcfg"))
    tenant_id: str = ""
    display_name: str = ""
    data_residency: str = "global"


@dataclass
class WorkspaceConfiguration(_VersionedConfig):
    """Per-workspace configuration.

    Overlays on top of the owning tenant's configuration. The effective config
    seen by runtime services is the deep-merge of tenant settings then workspace
    settings (workspace wins on key conflicts).
    """

    id: str = field(default_factory=lambda: new_id("wcfg"))
    workspace_id: str = ""
    tenant_id: str = ""
    display_name: str = ""


@dataclass
class SystemConfiguration(_VersionedConfig):
    """Platform-wide configuration (the global defaults / kill-switches).

    There is exactly one logical system configuration; it is the lowest layer
    in the configuration resolution order:
    system -> tenant -> workspace.
    """

    id: str = field(default_factory=lambda: new_id("scfg"))
    scope: str = "system"
