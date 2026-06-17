"""S1.8 Generation layer — public API.

Consumes the Context/Prompt Package produced by S1.7 and produces grounded,
citation-aware answers via a provider-agnostic LLM abstraction.
"""

from __future__ import annotations

from .contracts.errors import (
    ConfigurationError,
    ContractViolationError,
    GenerationError,
    GroundingError,
    PermanentProviderError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitedError,
    TransientProviderError,
    UnknownProviderError,
    UnsupportedAnswerError,
)
from .contracts.models import (
    AnswerStatus,
    BoundCitation,
    CitationRecord,
    ContextChunk,
    GeneratedAnswer,
    GenerationParams,
    LLMRequest,
    LLMResponse,
    PromptPackage,
    TokenUsage,
    new_request_id,
)
from .observability.metrics import (
    InMemoryMetricsSink,
    MetricsSink,
    NullMetricsSink,
)
from .pipeline.pipeline import GenerationPipeline
from .pipeline.retry import RetryPolicy
from .providers.base import (
    LLMProvider,
    create_provider,
    register_provider,
    registered_providers,
)

# Importing the concrete provider modules registers them in the registry.
from .providers import anthropic_provider as _anthropic  # noqa: F401
from .providers import mock as _mock  # noqa: F401
from .providers import openai_provider as _openai  # noqa: F401

__all__ = [
    "AnswerStatus",
    "BoundCitation",
    "CitationRecord",
    "ContextChunk",
    "GeneratedAnswer",
    "GenerationParams",
    "LLMRequest",
    "LLMResponse",
    "PromptPackage",
    "TokenUsage",
    "new_request_id",
    "GenerationError",
    "ProviderError",
    "TransientProviderError",
    "PermanentProviderError",
    "ProviderTimeoutError",
    "RateLimitedError",
    "ConfigurationError",
    "UnknownProviderError",
    "ContractViolationError",
    "GroundingError",
    "UnsupportedAnswerError",
    "LLMProvider",
    "create_provider",
    "register_provider",
    "registered_providers",
    "GenerationPipeline",
    "RetryPolicy",
    "MetricsSink",
    "NullMetricsSink",
    "InMemoryMetricsSink",
]

__version__ = "1.8.0"
