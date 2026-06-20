"""Domain models for the admin module."""
from __future__ import annotations

from ._base import (
    AuditCategory,
    DomainModel,
    GovernanceSeverity,
    HealthStatus,
    PolicyEffect,
    PolicyType,
    QuotaPeriod,
    QuotaScope,
    TenantState,
    WorkspaceState,
    new_id,
    utcnow,
)
from .audit import AdminAction, AuditEvent, PlatformHealth
from .configuration import (
    SystemConfiguration,
    TenantConfiguration,
    WorkspaceConfiguration,
)
from .feature_flag import FeatureFlag
from .policy import (
    AccessPolicy,
    GovernanceRule,
    Policy,
    RetentionPolicy,
)
from .quota import Quota, UsageLimit

__all__ = [
    # enums / base
    "AuditCategory",
    "DomainModel",
    "GovernanceSeverity",
    "HealthStatus",
    "PolicyEffect",
    "PolicyType",
    "QuotaPeriod",
    "QuotaScope",
    "TenantState",
    "WorkspaceState",
    "new_id",
    "utcnow",
    # models
    "AccessPolicy",
    "AdminAction",
    "AuditEvent",
    "FeatureFlag",
    "GovernanceRule",
    "Policy",
    "PlatformHealth",
    "Quota",
    "RetentionPolicy",
    "SystemConfiguration",
    "TenantConfiguration",
    "UsageLimit",
    "WorkspaceConfiguration",
]
