"""
Role model.

A Role is a named bundle of permissions assigned to users. PMOS ships five
default roles (ADMIN, MANAGER, ANALYST, VIEWER, SYSTEM) but the model supports
custom, per-tenant roles so enterprise customers can model their own org
structure.

Roles support permission inheritance via `inherits` so higher roles don't have
to re-list everything a lower role already grants. The RBAC engine flattens the
inheritance graph when evaluating a check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .permission import Permissions, WILDCARD


@dataclass
class Role:
    name: str
    permissions: Set[str] = field(default_factory=set)
    inherits: List[str] = field(default_factory=list)  # names of parent roles
    description: str = ""
    # tenant_id=None => global/system-defined role; otherwise a custom role
    # scoped to a single tenant.
    tenant_id: Optional[str] = None

    def grants(self, permission: str) -> bool:
        """Direct grant only (no inheritance — engine handles inheritance)."""
        return WILDCARD in self.permissions or permission in self.permissions

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "permissions": sorted(self.permissions),
            "inherits": list(self.inherits),
            "description": self.description,
            "tenant_id": self.tenant_id,
        }


class DefaultRoles:
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"
    SYSTEM = "SYSTEM"

    @staticmethod
    def build() -> Dict[str, Role]:
        """
        Construct the default role hierarchy.

            SYSTEM  -> wildcard (internal automation / orchestration)
            ADMIN   -> wildcard within a tenant (full tenant control)
            MANAGER -> manage users + connectors + run + view
            ANALYST -> run query/ingestion + view
            VIEWER  -> read-only
        """
        viewer = Role(
            name=DefaultRoles.VIEWER,
            permissions={Permissions.VIEW_DOCUMENTS, Permissions.VIEW_METRICS,
                         Permissions.RUN_QUERY},
            description="Read-only access.",
        )
        analyst = Role(
            name=DefaultRoles.ANALYST,
            permissions={Permissions.INGEST_DOCUMENTS, Permissions.RUN_INGESTION,
                         Permissions.RUN_REINDEX},
            inherits=[DefaultRoles.VIEWER],
            description="Can ingest and run workflows.",
        )
        manager = Role(
            name=DefaultRoles.MANAGER,
            permissions={Permissions.MANAGE_CONNECTORS, Permissions.MANAGE_USERS,
                         Permissions.MANAGE_API_KEYS},
            inherits=[DefaultRoles.ANALYST],
            description="Team management plus all analyst capability.",
        )
        admin = Role(
            name=DefaultRoles.ADMIN,
            permissions={WILDCARD, Permissions.ADMIN_ACCESS},
            description="Full control within a tenant.",
        )
        system = Role(
            name=DefaultRoles.SYSTEM,
            permissions={WILDCARD},
            description="Internal platform automation (orchestration, jobs).",
        )
        return {r.name: r for r in (viewer, analyst, manager, admin, system)}
