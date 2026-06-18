"""
User model.

A User belongs to exactly one tenant and (optionally) a default workspace, and
holds one or more roles that determine their permissions. Users never carry raw
permissions directly — only roles — so access is auditable and changeable in one
place.

The model deliberately stores no password/credential material: authentication is
delegated to pluggable auth providers (see `auth_provider.py`). PMOS only stores
the identity record and its authorization metadata.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any, Dict, List, Optional

from .role import DefaultRoles


class UserStatus(str, Enum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class User:
    tenant_id: str
    email: str
    display_name: str = ""
    user_id: str = field(default_factory=lambda: _new_id("usr"))
    workspace_id: Optional[str] = None
    status: UserStatus = UserStatus.ACTIVE
    roles: List[str] = field(default_factory=lambda: [DefaultRoles.VIEWER])
    created_at: float = field(default_factory=time)
    # external identity provider subject (set once SSO/OIDC is wired in)
    external_subject: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "email": self.email,
            "display_name": self.display_name,
            "status": self.status.value,
            "roles": list(self.roles),
            "created_at": self.created_at,
        }
