"""Stage 2: HeuristicExtractor.

Lower-precision fallback used only when Stage 1 produces nothing. It segments
the document into sentences, locates a verb-like pivot, and infers a
(subject, predicate, object) triple from the surrounding noun phrases.

This stage is intentionally conservative: it emits a fact only when it finds a
clear subject–verb–object shape, and tags atoms with heuristic confidence
(0.60–0.80).
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..confidence.scorer import ExtractionMethod
from ..contracts.atoms import FactAtom, SourceOffset
from ..contracts.results import ExtractionContext
from .base import Extractor, ExtractorOutput
from .text_utils import Sentence, normalize_phrase, looks_like_noun_phrase, segment_sentences

# A pragmatic list of relation verbs the heuristic recognizes as predicate
# pivots. Multi-word verbs are matched first (ordering matters).
_RELATION_VERBS = [
    "upgraded to", "downgraded to", "migrated to", "belongs to", "started at",
    "reports to", "acquired", "launched", "released", "joined", "uses", "owns",
    "manages", "supports", "replaced", "integrates with",
]

_VERB_ALTERNATION = "|".join(
    sorted((re.escape(v) for v in _RELATION_VERBS), key=len, reverse=True)
)
_PIVOT = re.compile(rf"\b(?P<verb>{_VERB_ALTERNATION})\b", re.IGNORECASE)


def _canonical_predicate(verb: str) -> str:
    return re.sub(r"\s+", "_", verb.strip().lower())


class HeuristicExtractor(Extractor):
    """Sentence-level relation inference (cascade stage 2)."""

    method = ExtractionMethod.HEURISTIC

    def __init__(self, base_confidence: float = 0.65):
        # Confidence stays inside the heuristic band; the scorer enforces this.
        self._base_confidence = base_confidence

    @property
    def name(self) -> str:
        return "heuristic"

    def extract(self, text: str, context: ExtractionContext) -> ExtractorOutput:
        atoms: List[FactAtom] = []
        if not text or not text.strip():
            return ExtractorOutput(method=self.method, atoms=[])

        doc_id = str(context.documentMetadata.get("documentId") or context.correlationId)

        for sentence in segment_sentences(text):
            triple = self._infer_triple(sentence)
            if triple is None:
                continue
            subject, predicate, obj = triple
            atoms.append(
                FactAtom(
                    tenantId=context.tenantId,
                    documentId=doc_id,
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    confidence=self._base_confidence,
                    sourceDocumentId=doc_id,
                    sourceOffsets=(SourceOffset(sentence.start, sentence.end),),
                )
            )
        return ExtractorOutput(method=self.method, atoms=atoms)

    def _infer_triple(self, sentence: Sentence) -> Optional[Tuple[str, str, str]]:
        match = _PIVOT.search(sentence.text)
        if not match:
            return None

        left = sentence.text[: match.start()].strip()
        right = sentence.text[match.end() :].strip()

        subject = normalize_phrase(self._last_clause(left))
        obj = normalize_phrase(self._first_clause(right))

        if not subject or not obj:
            return None
        if not looks_like_noun_phrase(subject) or not looks_like_noun_phrase(obj):
            return None

        predicate = _canonical_predicate(match.group("verb"))
        return (subject, predicate, obj)

    @staticmethod
    def _last_clause(span: str) -> str:
        # Subject is the noun phrase closest to the verb.
        parts = re.split(r"[,;:]", span)
        return parts[-1].strip() if parts else span

    @staticmethod
    def _first_clause(span: str) -> str:
        # Object terminates at the next punctuation or subordinating word.
        span = re.split(r"[.,;:!?]", span)[0]
        span = re.split(r"\b(?:on|in|at|because|after|since|when|which|that)\b", span)[0]
        return span.strip()
