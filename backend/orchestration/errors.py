"""
Orchestration error hierarchy.

All orchestration-specific exceptions derive from OrchestrationError so callers
(the API slice, CLI tools, monitoring) can catch the whole family with one
`except`. Errors are classified as RETRYABLE or TERMINAL to drive the
RetryManager's decision making.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class FailureClass(str, Enum):
    """Classification used by the retry subsystem to decide whether to retry."""

    RETRYABLE = "retryable"      # transient: timeouts, throttling, 5xx, network blips
    TERMINAL = "terminal"        # permanent: bad input, auth, logic errors
    UNKNOWN = "unknown"          # default conservative class (treated as terminal)


class OrchestrationError(Exception):
    """Base class for every error raised inside the orchestration layer."""

    failure_class: FailureClass = FailureClass.UNKNOWN

    def __init__(self, message: str, *, cause: Optional[BaseException] = None,
                 step: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.cause = cause
        self.step = step

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = self.message
        if self.step:
            base = f"[step={self.step}] {base}"
        if self.cause:
            base = f"{base} (caused by {type(self.cause).__name__}: {self.cause})"
        return base


# --- Retryable failures -----------------------------------------------------

class RetryableError(OrchestrationError):
    failure_class = FailureClass.RETRYABLE


class StepTimeoutError(RetryableError):
    """A workflow step exceeded its allotted wall-clock budget."""


class DependencyError(RetryableError):
    """A downstream module (retrieval, indexing, ...) was temporarily unavailable."""


class TransientModuleError(RetryableError):
    """A module raised an error flagged as transient (e.g. rate limit, 5xx)."""


# --- Terminal failures ------------------------------------------------------

class TerminalError(OrchestrationError):
    failure_class = FailureClass.TERMINAL


class WorkflowNotFoundError(TerminalError):
    """Requested workflow name is not present in the registry."""


class InvalidWorkflowInput(TerminalError):
    """The payload handed to a workflow failed validation."""


class ModuleFailureError(TerminalError):
    """A module raised a non-recoverable error."""


class PartialFailureError(OrchestrationError):
    """
    Some steps succeeded, some failed. Carries the partial result so callers can
    decide whether the degraded output is usable.
    """

    failure_class = FailureClass.TERMINAL

    def __init__(self, message: str, *, partial=None, **kwargs):
        super().__init__(message, **kwargs)
        self.partial = partial


# --- Lifecycle failures -----------------------------------------------------

class JobCancelledError(OrchestrationError):
    """Raised when a running job is cancelled cooperatively."""

    failure_class = FailureClass.TERMINAL


class RetryExhaustedError(OrchestrationError):
    """Raised after the maximum retry count is reached without success."""

    failure_class = FailureClass.TERMINAL

    def __init__(self, message: str, *, attempts: int = 0, last_error=None, **kwargs):
        super().__init__(message, **kwargs)
        self.attempts = attempts
        self.last_error = last_error


class InvalidStateTransition(TerminalError):
    """Attempted an illegal workflow state transition."""


def classify(exc: BaseException) -> FailureClass:
    """
    Best-effort classification of an arbitrary exception.

    Orchestration errors self-classify. Common Python/stdlib transient errors are
    mapped to RETRYABLE; everything else is conservatively TERMINAL.
    """
    if isinstance(exc, OrchestrationError):
        return exc.failure_class
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return FailureClass.RETRYABLE
    return FailureClass.TERMINAL
