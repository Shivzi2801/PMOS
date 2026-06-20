"""PMOS Wave 2 — Slice S2.5: Administration & Governance Module.

This package provides the administrative control plane for the PMOS platform:
configuration management, feature flags, a policy engine, quota enforcement,
tenant and workspace administration, an immutable audit framework, platform
health probes, a governance framework, and observability.

The public surface is intentionally curated so that callers in other slices
(ingestion, retrieval, generation, gateway) depend only on stable service
contracts rather than internal implementation modules.
"""
from __future__ import annotations

from .errors import (
    AdminError,
    ConfigurationError,
    ConfigurationNotFoundError,
    ConfigurationValidationError,
    ConfigurationVersionConflictError,
    FeatureFlagError,
    FeatureFlagNotFoundError,
    GovernanceError,
    GovernanceViolationError,
    PolicyError,
    PolicyEvaluationError,
    PolicyNotFoundError,
    QuotaError,
    QuotaExceededError,
    QuotaNotFoundError,
    TenantError,
    TenantNotFoundError,
    TenantStateError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceStateError,
)
from .models import (
    AccessPolicy,
    AdminAction,
    AuditEvent,
    FeatureFlag,
    GovernanceRule,
    Policy,
    PlatformHealth,
    Quota,
    RetentionPolicy,
    SystemConfiguration,
    TenantConfiguration,
    UsageLimit,
    WorkspaceConfiguration,
)
from .configuration_service import ConfigurationService
from .configuration_registry import ConfigurationRegistry
from .configuration_validator import ConfigurationValidator
from .feature_flag_service import FeatureFlagService
from .policy_engine import PolicyEngine
from .quota_service import QuotaService
from .tenant_admin_service import TenantAdminService
from .workspace_admin_service import WorkspaceAdminService
from .audit_service import AuditService
from .health_service import HealthService
from .governance_service import GovernanceService

__all__ = [
    # Errors
    "AdminError",
    "ConfigurationError",
    "ConfigurationNotFoundError",
    "ConfigurationValidationError",
    "ConfigurationVersionConflictError",
    "FeatureFlagError",
    "FeatureFlagNotFoundError",
    "GovernanceError",
    "GovernanceViolationError",
    "PolicyError",
    "PolicyEvaluationError",
    "PolicyNotFoundError",
    "QuotaError",
    "QuotaExceededError",
    "QuotaNotFoundError",
    "TenantError",
    "TenantNotFoundError",
    "TenantStateError",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspaceStateError",
    # Models
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
    # Services
    "ConfigurationService",
    "ConfigurationRegistry",
    "ConfigurationValidator",
    "FeatureFlagService",
    "PolicyEngine",
    "QuotaService",
    "TenantAdminService",
    "WorkspaceAdminService",
    "AuditService",
    "HealthService",
    "GovernanceService",
]

__version__ = "2.5.0"
