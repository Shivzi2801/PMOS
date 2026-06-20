"""Shared enums, value objects and base utilities for admin domain models."""
from __future__ import annotations

import enum
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp. Single source of 'now' for the module."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Generate a prefixed, sortable-ish unique identifier."""
    return f"{prefix}_{uuid.uuid4().hex}"


class TenantState(str, enum.Enum):
    """Lifecycle states for a tenant.

    Transitions (enforced by :class:`TenantAdminService`):
        PENDING   -> ACTIVE | DELETED
        ACTIVE    -> SUSPENDED | DELETED
        SUSPENDED -> ACTIVE | DELETED
        DELETED   -> (terminal)
    """

    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class WorkspaceState(str, enum.Enum):
    """Lifecycle states for a workspace.

    Transitions:
        ACTIVE   -> ARCHIVED | DELETED
        ARCHIVED -> ACTIVE | DELETED
        DELETED  -> (terminal)
    """

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class PolicyType(str, enum.Enum):
    ACCESS = "access"
    RETENTION = "retention"
    GOVERNANCE = "governance"


class PolicyEffect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class QuotaScope(str, enum.Enum):
    """The dimension a quota constrains."""

    REQUEST = "request"
    INGESTION = "ingestion"
    STORAGE = "storage"
    RETRIEVAL = "retrieval"
    GENERATION = "generation"


class QuotaPeriod(str, enum.Enum):
    """Rolling window over which a quota is measured."""

    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    TOTAL = "total"  # cumulative, never resets (e.g. storage)


class AuditCategory(str, enum.Enum):
    ADMIN_ACTION = "admin_action"
    CONFIGURATION = "configuration"
    POLICY = "policy"
    TENANT = "tenant"
    WORKSPACE = "workspace"
    SECURITY = "security"


class HealthStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class GovernanceSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DomainModel:
    """Base for serializable domain models.

    Provides a uniform :meth:`to_dict` that recursively converts enums to their
    values and datetimes to ISO-8601 strings, suitable for JSON transport and
    audit snapshots.
    """

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def _serialize(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value
