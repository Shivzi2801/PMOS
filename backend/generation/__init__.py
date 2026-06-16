"""Context Assembly layer (S1.7).

Converts S1.6 RetrievalResult objects into model-ready PromptPackage objects.
Pure domain layer: no LLM calls, no framework dependencies.
"""

from .citation_record import CitationRecord
from .context_assembler import ContextAssembler
from .context_filters import ACLFilter, DeduplicationFilter
from .context_package import (
    ContextChunk,
    ContextPackage,
    RetrievalResult,
    RetrievedChunk,
)
from .context_ranker import ContextRanker
from .errors import (
    CitationError,
    ContextError,
    EmptyContextError,
    InvalidRetrievalResultError,
    PromptTemplateError,
    TokenBudgetError,
)
from .metrics import AssemblyMetrics
from .prompt_builder import GroundedQATemplate, PromptBuilder
from .prompt_package import PromptMessage, PromptPackage
from .token_budget import TokenBudget, heuristic_token_counter

__all__ = [
    "CitationRecord",
    "ContextAssembler",
    "ACLFilter",
    "DeduplicationFilter",
    "ContextChunk",
    "ContextPackage",
    "RetrievalResult",
    "RetrievedChunk",
    "ContextRanker",
    "CitationError",
    "ContextError",
    "EmptyContextError",
    "InvalidRetrievalResultError",
    "PromptTemplateError",
    "TokenBudgetError",
    "AssemblyMetrics",
    "GroundedQATemplate",
    "PromptBuilder",
    "PromptMessage",
    "PromptPackage",
    "TokenBudget",
    "heuristic_token_counter",
]
