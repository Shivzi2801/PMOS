"""Domain errors for the Context Assembly layer (S1.7).

All errors derive from :class:`ContextError` so callers can catch the entire
domain surface with a single handler while still being able to discriminate
specific failure modes.
"""

from __future__ import annotations


class ContextError(Exception):
    """Base class for all Context Assembly errors."""


class InvalidRetrievalResultError(ContextError):
    """Raised when an incoming RetrievalResult violates the S1.6 contract."""


class TokenBudgetError(ContextError):
    """Raised when token budget configuration is invalid or unsatisfiable."""


class EmptyContextError(ContextError):
    """Raised when, after filtering/ranking, no chunks remain to assemble.

    Generation cannot proceed without context, so this is surfaced explicitly
    rather than producing an empty prompt.
    """


class PromptTemplateError(ContextError):
    """Raised when a prompt template is missing required fields or is unknown."""


class CitationError(ContextError):
    """Raised when a citation cannot be constructed from a chunk."""
