"""
backend/retrieval/errors.py

Retrieval Layer error hierarchy (S1.6).

Mirrors the error-handling conventions established in earlier slices
(S1.1 - S1.5): a single base exception per slice, structured context via a
``details`` mapping, and a stable ``code`` attribute that downstream systems
(and metrics) can switch on without depending on the human-readable message.

No external dependencies.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class RetrievalError(Exception):
    """
    Base class for all retrieval-layer errors.

    Attributes
    ----------
    code:
        Stable, machine-readable error code. Downstream code and metrics
        should branch on this rather than on the message text.
    message:
        Human-readable description.
    details:
        Optional structured context (query id, tenant, offending field, ...).
        Never include secrets or raw ACL principals beyond what the caller
        already possesses.
    """

    code: str = "retrieval_error"

    def __init__(
        self,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: Dict[str, Any] = dict(details or {})

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging / API surfaces."""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class InvalidQueryError(RetrievalError):
    """The query is malformed: empty text, bad pagination, illegal filter."""

    code = "invalid_query"


class TenantIsolationError(RetrievalError):
    """
    A tenant-isolation invariant was violated.

    Raised when a query lacks a tenant, or when a result that does not belong
    to the requesting tenant would otherwise be returned. This is a hard
    failure: it indicates a programming error or an attempted cross-tenant
    leak and must never be swallowed.
    """

    code = "tenant_isolation_violation"


class AclDeniedError(RetrievalError):
    """
    Raised when an explicit ACL check is requested and fails outright.

    Note: ordinary ACL *filtering* silently drops inaccessible documents and
    does NOT raise. This error is reserved for the case where a caller asks
    for a specific document by id and is not permitted to see it.
    """

    code = "acl_denied"


class IndexUnavailableError(RetrievalError):
    """The underlying index / vector store could not be reached or queried."""

    code = "index_unavailable"


class RerankError(RetrievalError):
    """A reranker failed. Callers may choose to fall back to base ranking."""

    code = "rerank_failed"


class QueryExpansionError(RetrievalError):
    """A query-expansion hook raised. Callers may fall back to the raw query."""

    code = "query_expansion_failed"


class PaginationError(InvalidQueryError):
    """Invalid pagination parameters (negative offset, oversized page, ...)."""

    code = "invalid_pagination"
