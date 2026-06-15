"""
PMOS S1.5 — Index Fan-Out
errors.py

Error taxonomy for the indexing subsystem.

Design notes
------------
All indexing errors derive from `IndexingError` so the orchestrator and
reconciler can catch the whole family at boundaries. Each error carries the
minimum context needed for triage (tenant_id, document_id, chunk_id) without
ever embedding chunk *content* — content may contain PII (see S1.2) and error
objects flow into logs/metrics that have a wider ACL than the source document.

Errors are classified along two axes:
  * `retryable`  — whether a retry has any chance of succeeding.
  * `category`   — coarse bucket used for metrics labels.

This mirrors the convention established in S1.1 (Connector SDK) where transient
vs. permanent failure is an explicit property rather than inferred from type.
"""

from __future__ import annotations

import enum
from typing import Optional


class ErrorCategory(str, enum.Enum):
    CHUNKING = "chunking"
    HASHING = "hashing"
    DEDUP = "dedup"
    VECTOR_STORE = "vector_store"
    FANOUT = "fanout"
    RECONCILER = "reconciler"
    CONTRACT = "contract"


class IndexingError(Exception):
    """Base class for all S1.5 indexing errors."""

    category: ErrorCategory = ErrorCategory.FANOUT
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        tenant_id: Optional[str] = None,
        document_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.document_id = document_id
        self.chunk_id = chunk_id

    def context(self) -> dict:
        """ACL-safe context for logs/metrics. Never includes content."""
        return {
            "category": self.category.value,
            "retryable": self.retryable,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"{type(self).__name__}(message={self.message!r}, "
            f"tenant_id={self.tenant_id!r}, document_id={self.document_id!r}, "
            f"chunk_id={self.chunk_id!r}, retryable={self.retryable})"
        )


# --- Contract / validation -------------------------------------------------

class ContractViolation(IndexingError):
    """A chunk or document failed required-field / invariant validation."""

    category = ErrorCategory.CONTRACT
    retryable = False


# --- Chunking ---------------------------------------------------------------

class ChunkingError(IndexingError):
    category = ErrorCategory.CHUNKING
    retryable = False


class EmptyDocumentError(ChunkingError):
    """Document had no indexable content after normalization."""


# --- Hashing ----------------------------------------------------------------

class HashingError(IndexingError):
    category = ErrorCategory.HASHING
    retryable = False


class HashCollisionError(HashingError):
    """
    Two chunks produced the same SHA256 content_hash but differ in content.

    A genuine SHA256 collision is computationally infeasible; in practice this
    is raised when a content_hash is reused for *different* bytes, which signals
    a bug (e.g. hash computed over the wrong field) or a tampered payload. It is
    NEVER retryable and must surface to the reconciliation report.
    """

    category = ErrorCategory.HASHING
    retryable = False


# --- Dedup ------------------------------------------------------------------

class DedupError(IndexingError):
    category = ErrorCategory.DEDUP
    retryable = False


# --- Vector store -----------------------------------------------------------

class VectorStoreError(IndexingError):
    category = ErrorCategory.VECTOR_STORE
    retryable = True


class VectorStoreUnavailable(VectorStoreError):
    """Transient connectivity / capacity failure. Retry with backoff."""

    retryable = True


class VectorStoreRejected(VectorStoreError):
    """
    Store rejected the payload (schema mismatch, oversize vector, bad partition
    key). Retrying the identical payload will not help.
    """

    retryable = False


# --- Fan-out ----------------------------------------------------------------

class PartialIndexError(IndexingError):
    """
    Some indexes accepted the chunk set and others did not. Carries per-target
    outcomes so the orchestrator can record what succeeded and retry only the
    failed targets (no double-write to healthy targets).
    """

    category = ErrorCategory.FANOUT
    retryable = True

    def __init__(self, message: str, *, failures: dict, **kwargs) -> None:
        super().__init__(message, **kwargs)
        # failures: {target_name: IndexingError}
        self.failures = failures

    def context(self) -> dict:
        ctx = super().context()
        ctx["failed_targets"] = sorted(self.failures.keys())
        return ctx


# --- Reconciler -------------------------------------------------------------

class ReconciliationError(IndexingError):
    category = ErrorCategory.RECONCILER
    retryable = True
