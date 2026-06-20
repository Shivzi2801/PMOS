"""Domain-specific exceptions for the Administration & Governance module.

All exceptions derive from :class:`AdminError`, allowing callers to catch the
entire admin domain with a single handler while still being able to branch on
specific failure modes. Every exception carries a machine-readable ``code`` and
an optional ``details`` mapping so that the API gateway can translate them into
structured error responses without string parsing.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional


class AdminError(Exception):
    """Base class for every error raised by the admin module.

    Attributes:
        code: Stable, machine-readable identifier (e.g. ``"quota_exceeded"``).
        message: Human-readable description.
        details: Optional structured context for the caller / logs.
    """

    code: str = "admin_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a transport-friendly dict for API responses."""
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class ConfigurationError(AdminError):
    code = "configuration_error"
    http_status = 400


class ConfigurationNotFoundError(ConfigurationError):
    code = "configuration_not_found"
    http_status = 404


class ConfigurationValidationError(ConfigurationError):
    code = "configuration_invalid"
    http_status = 422


class ConfigurationVersionConflictError(ConfigurationError):
    """Raised on optimistic-concurrency version mismatch during update."""

    code = "configuration_version_conflict"
    http_status = 409


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #
class FeatureFlagError(AdminError):
    code = "feature_flag_error"
    http_status = 400


class FeatureFlagNotFoundError(FeatureFlagError):
    code = "feature_flag_not_found"
    http_status = 404


# --------------------------------------------------------------------------- #
# Policies
# --------------------------------------------------------------------------- #
class PolicyError(AdminError):
    code = "policy_error"
    http_status = 400


class PolicyNotFoundError(PolicyError):
    code = "policy_not_found"
    http_status = 404


class PolicyEvaluationError(PolicyError):
    """Raised when a policy cannot be evaluated (malformed rule, bad context)."""

    code = "policy_evaluation_failed"
    http_status = 422


# --------------------------------------------------------------------------- #
# Quotas
# --------------------------------------------------------------------------- #
class QuotaError(AdminError):
    code = "quota_error"
    http_status = 400


class QuotaNotFoundError(QuotaError):
    code = "quota_not_found"
    http_status = 404


class QuotaExceededError(QuotaError):
    """Raised when an operation would push usage past an enforced limit."""

    code = "quota_exceeded"
    http_status = 429

    def __init__(
        self,
        message: str,
        *,
        quota_key: str,
        limit: float,
        current: float,
        requested: float,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        merged = {
            "quota_key": quota_key,
            "limit": limit,
            "current": current,
            "requested": requested,
            **(dict(details) if details else {}),
        }
        super().__init__(message, details=merged)
        self.quota_key = quota_key
        self.limit = limit
        self.current = current
        self.requested = requested


# --------------------------------------------------------------------------- #
# Tenants
# --------------------------------------------------------------------------- #
class TenantError(AdminError):
    code = "tenant_error"
    http_status = 400


class TenantNotFoundError(TenantError):
    code = "tenant_not_found"
    http_status = 404


class TenantStateError(TenantError):
    """Raised on an illegal tenant lifecycle transition."""

    code = "tenant_invalid_state"
    http_status = 409


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #
class WorkspaceError(AdminError):
    code = "workspace_error"
    http_status = 400


class WorkspaceNotFoundError(WorkspaceError):
    code = "workspace_not_found"
    http_status = 404


class WorkspaceStateError(WorkspaceError):
    code = "workspace_invalid_state"
    http_status = 409


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #
class GovernanceError(AdminError):
    code = "governance_error"
    http_status = 400


class GovernanceViolationError(GovernanceError):
    """Raised when an action violates one or more governance rules."""

    code = "governance_violation"
    http_status = 403

    def __init__(
        self,
        message: str,
        *,
        violations: list[dict[str, Any]],
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        merged = {"violations": violations, **(dict(details) if details else {})}
        super().__init__(message, details=merged)
        self.violations = violations
