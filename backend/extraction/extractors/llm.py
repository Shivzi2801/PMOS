"""Stage 3: LLMExtractor (interface only).

Slice 1.3 defines the contract for LLM-backed extraction but DOES NOT integrate
any model. A concrete implementation in a future slice will route requests
through the Gateway. The pipeline must remain fully functional without it.

The ``NullLLMExtractor`` below is a safe, no-op implementation used as the
default so the cascade can reference a stage-3 object without making LLM calls.
"""

from __future__ import annotations

import abc
from typing import List

from ..confidence.scorer import ExtractionMethod
from ..contracts.atoms import Atom
from ..contracts.results import ExtractionContext
from .base import Extractor, ExtractorOutput


class LLMExtractor(Extractor, abc.ABC):
    """Abstract interface for model-backed extraction.

    Future implementations will:
      * build a prompt from ``text`` and ``context``
      * route the request through the Gateway (model selection, quotas, PII
        guardrails)
      * parse the structured response into atoms
      * assign confidence within the LLM band (0.70–0.95)

    NONE of this happens in Slice 1.3.
    """

    method = ExtractionMethod.LLM

    @property
    def name(self) -> str:
        return "llm"

    @abc.abstractmethod
    def extract(self, text: str, context: ExtractionContext) -> ExtractorOutput:
        """Defined by future Gateway-wired implementations."""


class NullLLMExtractor(LLMExtractor):
    """Default stage-3 placeholder. Emits nothing and performs no I/O."""

    @property
    def name(self) -> str:
        return "llm_null"

    def extract(self, text: str, context: ExtractionContext) -> ExtractorOutput:
        empty: List[Atom] = []
        return ExtractorOutput(method=self.method, atoms=empty)
