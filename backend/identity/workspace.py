"""
Workspace model.

A Workspace is a sub-division *inside* a tenant (Product Team, Support Team,
Engineering Team). It is the second level of the isolation hierarchy:

    Tenant ──< Workspace ──< User / data

Workspaces let one customer separate concerns (different teams, environments, or
data domains) while still rolling up to a single billing/admin boundary. Every
workspace stores its owning `tenant_id`; cross-tenant workspace access is a hard
isolation violation enforced by the tenant guard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any, Dict


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Workspace:
    tenant_id: str
    workspace_name: str
    workspace_id: str = field(default_factory=lambda: _new_id("wsp"))
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    created_at: float = field(default_factory=time)
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == WorkspaceStatus.ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "tenant_id": self.tenant_id,
            "workspace_name": self.workspace_name,
            "status": self.status.value,
            "created_at": self.created_at,
            "settings": dict(self.settings),
        }
