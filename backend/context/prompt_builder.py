"""Prompt construction for context assembly (S1.7).

:class:`PromptBuilder` turns a :class:`ContextPackage` into a
:class:`PromptPackage`. Templates are pluggable via a registry so future prompt
formats can be added without modifying the builder or the assembler.

A template is any callable implementing :class:`PromptTemplate`. The default
``"qa_grounded"`` template produces a system+user message pair that instructs
the model to answer strictly from the provided, citation-marked context.

No LLM is called here; the builder only assembles text and token estimates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Protocol

from .context_package import ContextPackage
from .errors import PromptTemplateError
from .prompt_package import PromptMessage, PromptPackage
from .token_budget import TokenBudget


class PromptTemplate(Protocol):
    """Protocol for prompt templates."""

    name: str

    def build(
        self, context: ContextPackage, budget: TokenBudget
    ) -> List[PromptMessage]:  # pragma: no cover - structural
        ...


@dataclass(frozen=True)
class GroundedQATemplate:
    """Default template: grounded question answering with inline citations."""

    name: str = "qa_grounded"
    system_preamble: str = (
        "You are a precise assistant. Answer the user's question using ONLY "
        "the numbered context passages provided. Cite supporting passages "
        "inline using their bracketed markers, e.g. [1]. If the context does "
        "not contain the answer, say so explicitly and do not invent facts."
    )

    def build(
        self, context: ContextPackage, budget: TokenBudget
    ) -> List[PromptMessage]:
        context_block = context.render_context_block()
        citations_block = context.render_citations_block()
        user_content = (
            f"Question:\n{context.query}\n\n"
            f"Context passages:\n{context_block}\n\n"
            f"Sources:\n{citations_block}"
        )
        return [
            PromptMessage(role="system", content=self.system_preamble),
            PromptMessage(role="user", content=user_content),
        ]


# A factory builds a fresh template instance on demand.
TemplateFactory = Callable[[], PromptTemplate]


@dataclass
class PromptBuilder:
    """Builds PromptPackages from ContextPackages using named templates."""

    budget: TokenBudget
    _templates: Dict[str, TemplateFactory] = field(default_factory=dict)
    default_template: str = "qa_grounded"

    def __post_init__(self) -> None:
        if not self._templates:
            self._templates = {GroundedQATemplate().name: GroundedQATemplate}

    def register_template(self, name: str, factory: TemplateFactory) -> None:
        """Register (or override) a template factory under ``name``."""
        self._templates[name] = factory

    def available_templates(self) -> List[str]:
        return sorted(self._templates)

    def build(
        self, context: ContextPackage, template_name: str | None = None
    ) -> PromptPackage:
        name = template_name or self.default_template
        factory = self._templates.get(name)
        if factory is None:
            raise PromptTemplateError(
                f"unknown prompt template '{name}'; "
                f"available: {self.available_templates()}"
            )
        template = factory()
        messages = template.build(context, self.budget)
        estimated = sum(self.budget.count(m.content) for m in messages)
        return PromptPackage(
            messages=tuple(messages),
            citations=tuple(context.citations),
            query=context.query,
            template_name=name,
            estimated_prompt_tokens=estimated,
            max_response_tokens=self.budget.reserved_response,
            metadata=dict(context.metadata),
        )
