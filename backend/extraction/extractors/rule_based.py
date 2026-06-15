"""Stage 1: RuleBasedExtractor.

Deterministic, high-precision extraction using a registry of regex sentence
templates. Each template maps named capture groups to (subject, predicate,
object). Because matches are deterministic and tightly constrained, atoms from
this stage receive rule-based confidence (>= 0.90).

Templates are data, not code: adding a new pattern is a one-line registration,
and future atom types can register their own template sets against the same
engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Pattern

from ..confidence.scorer import ExtractionMethod
from ..contracts.atoms import FactAtom, SourceOffset
from ..contracts.results import ExtractionContext
from .base import Extractor, ExtractorOutput


@dataclass(frozen=True)
class FactTemplate:
    """A regex template producing a FactAtom.

    The regex must expose named groups ``subject`` and ``object``. The
    ``predicate`` is a fixed canonical relation label for the template.
    """

    name: str
    pattern: Pattern[str]
    predicate: str

    @classmethod
    def build(cls, name: str, regex: str, predicate: str) -> "FactTemplate":
        return cls(name=name, pattern=re.compile(regex, re.IGNORECASE), predicate=predicate)


# Canonical sentence templates. The capture groups intentionally stop at
# clause boundaries (punctuation / conjunctions) to avoid greedy over-capture.
# Subject/object spans must not cross a sentence boundary, so a period
# followed by whitespace is excluded from the character class.
_OBJ = r"(?P<object>[A-Z0-9][\w&'-]*(?:[ .][\w&'-]+)*?)"
_SUBJ = r"(?P<subject>[A-Z0-9][\w&'-]*(?:[ ][\w&'-]+)*?)"

DEFAULT_TEMPLATES: List[FactTemplate] = [
    FactTemplate.build(
        "upgraded_to",
        rf"\b{_SUBJ}\s+upgraded\s+to\s+{_OBJ}(?=[.,;!?]|\s+(?:on|in|at|because|after)\b|$)",
        "upgraded_to",
    ),
    FactTemplate.build(
        "downgraded_to",
        rf"\b{_SUBJ}\s+downgraded\s+to\s+{_OBJ}(?=[.,;!?]|\s+(?:on|in|at|because|after)\b|$)",
        "downgraded_to",
    ),
    FactTemplate.build(
        "belongs_to",
        rf"\b{_SUBJ}\s+belongs\s+to\s+{_OBJ}(?=[.,;!?]|$)",
        "belongs_to",
    ),
    FactTemplate.build(
        "started_at",
        rf"\b{_SUBJ}\s+started\s+at\s+{_OBJ}(?=[.,;!?]|$)",
        "started_at",
    ),
    FactTemplate.build(
        "is_a",
        rf"\b{_SUBJ}\s+is\s+(?:a|an)\s+{_OBJ}(?=[.,;!?]|$)",
        "is_a",
    ),
    FactTemplate.build(
        "acquired",
        rf"\b{_SUBJ}\s+acquired\s+{_OBJ}(?=[.,;!?]|\s+(?:on|in|for)\b|$)",
        "acquired",
    ),
]


class RuleBasedExtractor(Extractor):
    """Template-driven, deterministic fact extractor (cascade stage 1)."""

    method = ExtractionMethod.RULE_BASED

    def __init__(self, templates: Optional[List[FactTemplate]] = None):
        self._templates = templates if templates is not None else list(DEFAULT_TEMPLATES)

    @property
    def name(self) -> str:
        return "rule_based"

    def extract(self, text: str, context: ExtractionContext) -> ExtractorOutput:
        atoms: List[FactAtom] = []
        if not text or not text.strip():
            return ExtractorOutput(method=self.method, atoms=[])

        doc_id = str(context.documentMetadata.get("documentId", "")) or _meta_doc_id(context)

        for template in self._templates:
            for match in template.pattern.finditer(text):
                subject = _clean(match.group("subject"))
                obj = _clean(match.group("object"))
                if not subject or not obj:
                    continue
                offset = SourceOffset(start=match.start(), end=match.end())
                atoms.append(
                    FactAtom(
                        tenantId=context.tenantId,
                        documentId=doc_id,
                        subject=subject,
                        predicate=template.predicate,
                        object=obj,
                        confidence=0.92,  # band default; scorer validates downstream
                        sourceDocumentId=doc_id,
                        sourceOffsets=(offset,),
                    )
                )
        return ExtractorOutput(method=self.method, atoms=atoms)


def _clean(value: str) -> str:
    return " ".join(value.strip().split()).strip(" .,;:")


def _meta_doc_id(context: ExtractionContext) -> str:
    # The pipeline injects the real documentId into metadata before calling
    # extractors; fall back to correlationId only if absent (defensive).
    return str(context.documentMetadata.get("documentId") or context.correlationId)
