"""
errors.py
=========

Exception hierarchy for the Grounding & Answer Verification slice (S1.9).

WHY THIS FILE EXISTS
--------------------
Grounding is the last line of defense before an answer reaches an enterprise
user. When something goes wrong inside this layer we must fail in a *predictable,
typed* way so that the calling layer (generation / API) can decide whether to:

  * degrade gracefully (return the answer with a low-confidence flag),
  * retry the verification,
  * or reject the answer outright.

Using a single base class (`GroundingError`) lets callers catch the entire
family with one `except` while still allowing fine-grained handling of specific
failure modes.

DESIGN NOTES
------------
* Every exception carries a machine-readable `code` so logs and metrics can be
  aggregated without string-parsing the message.
* `details` is an optional structured payload (dict) used for debugging and for
  the audit trail. It should never contain secrets.
* Errors are intentionally cheap to construct; they perform no I/O.
"""

from __future__ import annotations

from typing import Any, Optional


class GroundingError(Exception):
    """Base class for all grounding-layer errors.

    Attributes
    ----------
    code:
        A stable, machine-readable identifier (e.g. ``"EMPTY_EVIDENCE"``).
    message:
        Human-readable description.
    details:
        Optional structured context useful for debugging / auditing.
    """

    code: str = "GROUNDING_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialise the error for logging / audit records."""
        return {
            "error": self.__class__.__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.__class__.__name__}(code={self.code!r}, message={self.message!r})"


# --------------------------------------------------------------------------- #
# Input / data integrity errors
# --------------------------------------------------------------------------- #
class MissingCitationsError(GroundingError):
    """Raised when an answer that requires citations has none at all.

    This is distinct from "some claims are unsupported" (which is a normal,
    expected outcome). This error means the answer payload was structurally
    missing the citation field entirely.
    """

    code = "MISSING_CITATIONS"


class EmptyEvidenceError(GroundingError):
    """Raised when the retrieval evidence set is empty.

    Without evidence there is nothing to ground against. Depending on policy
    the pipeline may degrade to REJECTED rather than raising, but the matcher
    raises this so the pipeline can make that decision explicitly.
    """

    code = "EMPTY_EVIDENCE"


class MalformedClaimError(GroundingError):
    """Raised when a claim cannot be parsed / is structurally invalid."""

    code = "MALFORMED_CLAIM"


class MalformedAnswerError(GroundingError):
    """Raised when the generated answer payload is unusable (e.g. None/empty)."""

    code = "MALFORMED_ANSWER"


# --------------------------------------------------------------------------- #
# Processing / runtime errors
# --------------------------------------------------------------------------- #
class VerificationTimeoutError(GroundingError):
    """Raised when verification exceeds the configured deadline."""

    code = "VERIFICATION_TIMEOUT"


class ConfidenceComputationError(GroundingError):
    """Raised when the confidence scorer fails to produce a valid score."""

    code = "CONFIDENCE_COMPUTATION_FAILED"


class EvidenceMatchingError(GroundingError):
    """Raised when evidence matching fails unexpectedly."""

    code = "EVIDENCE_MATCHING_FAILED"


class ClaimExtractionError(GroundingError):
    """Raised when claim extraction fails unexpectedly."""

    code = "CLAIM_EXTRACTION_FAILED"


# --------------------------------------------------------------------------- #
# Persistence errors
# --------------------------------------------------------------------------- #
class AuditWriteError(GroundingError):
    """Raised when the audit trail cannot be persisted.

    Audit failures are serious for compliance but should NOT lose the user's
    answer. The pipeline catches this and degrades gracefully while logging
    loudly.
    """

    code = "AUDIT_WRITE_FAILED"
