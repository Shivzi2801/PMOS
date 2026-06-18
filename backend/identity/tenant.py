"""
Tenant model.

A Tenant is the top-level isolation boundary in PMOS — it represents a paying
customer / company (Acme Corp, Deloitte, Microsoft). Every other entity
(workspace, user, API key, session, audit record) is owned by exactly one
tenant, and the `tenant_id` is the key that every downstream slice partitions
its data by.

This module defines the data shape and lifecycle status only; storage and the
business operations live in `identity_service.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any, Dict


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"      # billing/compliance hold — auth blocked
    DEPROVISIONING = "deprovisioning"  # being torn down
    ARCHIVED = "archived"        # read-only historical


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Tenant:
    tenant_name: str
    tenant_id: str = field(default_factory=lambda: _new_id("tnt"))
    status: TenantStatus = TenantStatus.ACTIVE
    created_at: float = field(default_factory=time)
    # Free-form per-tenant configuration: feature flags, data-residency region,
    # default retry policy, quotas, branding, etc. Designed for extensibility.
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant_name,
            "status": self.status.value,
            "created_at": self.created_at,
            "settings": dict(self.settings),
        }
