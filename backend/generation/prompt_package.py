"""Prompt package — the terminal artifact of the Context Assembly layer (S1.7).

A :class:`PromptPackage` is a model-ready, transport-neutral description of a
prompt. It deliberately does NOT call any LLM and carries no framework types;
Wave 2 (Generation) consumes it and adapts it to whatever provider API is in
use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .citation_record import CitationRecord


@dataclass(frozen=True)
class PromptMessage:
    """A single role-tagged message in the prompt.

    Roles follow the conventional ``system`` / ``user`` / ``assistant`` scheme
    used by chat-style models, but are kept as plain strings to avoid coupling
    to any provider enum.
    """

    role: str
    content: str


@dataclass(frozen=True)
class PromptPackage:
    """A complete, model-ready prompt produced from a ContextPackage.

    Attributes:
        messages: Ordered role-tagged messages.
        citations: Citation records referenced by the prompt body.
        query: The originating user query, for traceability.
        template_name: Name of the template used to build the prompt.
        estimated_prompt_tokens: Estimated token cost of the prompt messages.
        max_response_tokens: Tokens reserved for the model's response.
        metadata: Passthrough metadata accumulated through the pipeline.
    """

    messages: Sequence[PromptMessage]
    citations: Sequence[CitationRecord]
    query: str
    template_name: str
    estimated_prompt_tokens: int
    max_response_tokens: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_message_dicts(self) -> list[dict[str, str]]:
        """Return messages as plain dicts (convenient for provider adapters)."""
        return [{"role": m.role, "content": m.content} for m in self.messages]
