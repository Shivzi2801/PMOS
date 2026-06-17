"""Token budgeting for context assembly (S1.7).

Provides a pluggable token-counting abstraction plus a :class:`TokenBudget`
value object that partitions a model's context window into reserved regions
(system, query, citations, response) and a remaining region available for
retrieved context.

No external tokenizer dependency is taken. A deterministic heuristic estimator
is used by default; callers may inject their own counter (e.g. a real BPE
tokenizer) via the ``token_counter`` callable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .errors import TokenBudgetError

# A token counter maps raw text to an integer token count.
TokenCounter = Callable[[str], int]


def heuristic_token_counter(text: str) -> int:
    """Estimate token count without external dependencies.

    Uses an approximation of ~4 characters per token, which is a reasonable
    rough proxy for English BPE tokenizers. Always returns at least 1 token
    for non-empty text so empty budgets are never silently produced.
    """
    if not text:
        return 0
    # Blend char- and word-based estimates for stability across inputs.
    char_estimate = (len(text) + 3) // 4
    word_estimate = len(text.split())
    return max(1, char_estimate, word_estimate)


@dataclass(frozen=True)
class TokenBudget:
    """Immutable partitioning of a model context window.

    Attributes:
        max_context_window: Total tokens the target model can accept.
        reserved_system: Tokens reserved for the system/instruction preamble.
        reserved_query: Tokens reserved for the user query.
        reserved_citations: Tokens reserved for the rendered citation block.
        reserved_response: Tokens held back for the model's generated answer.
    """

    max_context_window: int
    reserved_system: int = 0
    reserved_query: int = 0
    reserved_citations: int = 0
    reserved_response: int = 0
    token_counter: TokenCounter = field(default=heuristic_token_counter, repr=False)

    def __post_init__(self) -> None:
        if self.max_context_window <= 0:
            raise TokenBudgetError("max_context_window must be positive")
        for name in (
            "reserved_system",
            "reserved_query",
            "reserved_citations",
            "reserved_response",
        ):
            value = getattr(self, name)
            if value < 0:
                raise TokenBudgetError(f"{name} must be non-negative")
        if self.total_reserved >= self.max_context_window:
            raise TokenBudgetError(
                "reserved tokens exceed or equal the context window; "
                "no room remains for retrieved context"
            )

    @property
    def total_reserved(self) -> int:
        return (
            self.reserved_system
            + self.reserved_query
            + self.reserved_citations
            + self.reserved_response
        )

    @property
    def available_for_context(self) -> int:
        """Tokens available for retrieved context after all reservations."""
        return self.max_context_window - self.total_reserved

    def count(self, text: str) -> int:
        """Count tokens for ``text`` using the configured counter."""
        return self.token_counter(text)

    def fits(self, used_tokens: int) -> bool:
        """Return True if ``used_tokens`` fits the context allotment."""
        return used_tokens <= self.available_for_context
