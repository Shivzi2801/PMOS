"""
Identity / access-control error hierarchy.

Every failure mode the slice must handle has a dedicated, catchable exception so
the API and orchestration layers can map them to precise HTTP statuses and audit
records. Errors carry an HTTP-style `code` and a `category` so callers don't have
to pattern-match on type when they only need a coarse signal.

Required failure modes (from the spec) and their classes:
    unknown tenant         -> UnknownTenantError
    missing workspace      -> WorkspaceNotFoundError
    invalid user           -> InvalidUserError
    expired session        -> SessionExpiredError
    invalid api key        -> InvalidAPIKeyError
    permission denied       -> PermissionDeniedError
    tenant mismatch        -> TenantMismatchError
    authorization failure  -> AuthorizationError (base for authz failures)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCategory(str, Enum):
    NOT_FOUND = "not_found"          # entity does not exist
    UNAUTHENTICATED = "unauthenticated"  # who are you? (no/!valid credential)
    UNAUTHORIZED = "unauthorized"    # known identity, insufficient rights
    ISOLATION = "isolation"          # cross-tenant boundary violation
    INVALID = "invalid"              # malformed / inconsistent input
    CONFLICT = "conflict"            # state conflict (e.g. duplicate)


class IdentityError(Exception):
    """Base for everything raised by the identity slice."""

    code: int = 400
    category: ErrorCategory = ErrorCategory.INVALID

    def __init__(self, message: str, *, detail: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "error": type(self).__name__,
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
        }


# --- Not found --------------------------------------------------------------

class UnknownTenantError(IdentityError):
    code = 404
    category = ErrorCategory.NOT_FOUND


class WorkspaceNotFoundError(IdentityError):
    code = 404
    category = ErrorCategory.NOT_FOUND


class InvalidUserError(IdentityError):
    code = 404
    category = ErrorCategory.NOT_FOUND


class RoleNotFoundError(IdentityError):
    code = 404
    category = ErrorCategory.NOT_FOUND


# --- Authentication ---------------------------------------------------------

class AuthenticationError(IdentityError):
    code = 401
    category = ErrorCategory.UNAUTHENTICATED


class InvalidAPIKeyError(AuthenticationError):
    pass


class SessionExpiredError(AuthenticationError):
    pass


class SessionNotFoundError(AuthenticationError):
    pass


# --- Authorization ----------------------------------------------------------

class AuthorizationError(IdentityError):
    code = 403
    category = ErrorCategory.UNAUTHORIZED


class PermissionDeniedError(AuthorizationError):
    pass


class InactiveEntityError(AuthorizationError):
    """Tenant / workspace / user exists but is suspended or disabled."""


# --- Tenant isolation -------------------------------------------------------

class TenantMismatchError(IdentityError):
    code = 403
    category = ErrorCategory.ISOLATION


class CrossTenantAccessError(TenantMismatchError):
    """A subject from tenant A tried to touch a resource owned by tenant B."""


# --- Conflict ---------------------------------------------------------------

class DuplicateEntityError(IdentityError):
    code = 409
    category = ErrorCategory.CONFLICT


class MissingTenantContextError(IdentityError):
    """No tenant context was established for an operation that requires one."""

    code = 400
    category = ErrorCategory.INVALID
