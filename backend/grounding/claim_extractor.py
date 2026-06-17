"""
claim_extractor.py
==================

Extract atomic, independently verifiable claims from a generated answer.

WHY THIS FILE EXISTS
--------------------
Verification operates at the granularity of *claims*, not whole answers. A
single answer like:

    "The customer requested a refund on May 4. The refund was approved on May 6."

contains two facts that can each be true or false independently. Grounding each
one separately lets us say "claim 1 is supported, claim 2 is not" instead of a
useless all-or-nothing verdict.

RESPONSIBILITIES
----------------
* Segment answer text into sentence-level claims.
* Filter out non-factual fragments (greetings, hedges, pure questions).
* Record the character span of each claim for highlighting / audit replay.
* Attach any citations whose answer-span overlaps the claim as *hints*.

INPUTS
------
* answer_text: str
* citations: list[Citation]  (optional hints from the generator)

OUTPUTS
-------
* list[Claim]

DESIGN DECISIONS
----------------
* We use a deterministic, dependency-free sentence segmenter. Heavy NLP models
  are intentionally avoided here: extraction must be fast, predictable, and
  reproducible for audits. The segmentation rules are conservative and handle
  common abbreviations and decimal numbers so we don't split mid-sentence.
* claim_id is deterministic (``<answer_id>::c<index>``) so the same answer
  always yields the same ids — essential for audit replay.
"""

from __future__ import annotations

import re
from typing import Optional

from .errors import ClaimExtractionError, MalformedAnswerError
from .grounding_result import Citation, Claim


# Sentence-ending punctuation followed by whitespace + capital/quote/digit.
_SENTENCE_BOUNDARY = re.compile(
    r"""
    (?<=[.!?])        # preceded by sentence punctuation
    [\"')\]]*         # optional closing quotes/brackets
    \s+               # whitespace
    (?=[A-Z0-9"'(\[]) # followed by a likely sentence start
    """,
    re.VERBOSE,
)

# Common abbreviations that must NOT trigger a sentence split.
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.",
    "e.g.", "i.e.", "etc.", "inc.", "ltd.", "co.", "corp.", "no.",
    "fig.", "approx.", "dept.", "est.",
}

# Fragments that are not factual claims worth verifying.
_NON_FACTUAL_PREFIXES = (
    "thanks", "thank you", "please", "hello", "hi ", "sure", "of course",
    "let me know", "feel free", "i hope", "i'm happy", "i am happy",
)


class ClaimExtractor:
    """Splits answer text into atomic claims."""

    def __init__(self, min_claim_length: int = 4) -> None:
        # Claims shorter than this many characters are treated as noise.
        self.min_claim_length = min_claim_length

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def extract(
        self,
        answer_text: str,
        answer_id: str,
        citations: Optional[list[Citation]] = None,
    ) -> list[Claim]:
        """Extract claims from ``answer_text``.

        Raises
        ------
        MalformedAnswerError
            If the answer text is None or blank.
        ClaimExtractionError
            If an unexpected error occurs during segmentation.
        """
        if answer_text is None or not answer_text.strip():
            raise MalformedAnswerError(
                "Answer text is empty or None.",
                details={"answer_id": answer_id},
            )

        citations = citations or []
        try:
            sentences = self._segment(answer_text)
        except Exception as exc:  # defensive: regex should not raise, but be safe
            raise ClaimExtractionError(
                "Failed to segment answer into sentences.",
                details={"answer_id": answer_id, "cause": str(exc)},
            ) from exc

        claims: list[Claim] = []
        index = 0
        for text, start, end in sentences:
            cleaned = text.strip()
            if not self._is_factual(cleaned):
                continue
            claim = Claim(
                claim_id=f"{answer_id}::c{index}",
                text=cleaned,
                source_span=(start, end),
                supporting_citations=self._citations_for_span(
                    (start, end), citations
                ),
            )
            claims.append(claim)
            index += 1

        return claims

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _segment(self, text: str) -> list[tuple[str, int, int]]:
        """Split into (sentence, start_offset, end_offset) triples.

        We split on the regex then re-merge any split that occurred right after
        a known abbreviation (false positive).
        """
        # Build raw candidate boundaries with offsets.
        pieces: list[tuple[str, int, int]] = []
        last = 0
        for match in _SENTENCE_BOUNDARY.finditer(text):
            boundary = match.start()
            segment = text[last:boundary]
            pieces.append((segment, last, boundary))
            last = match.end()
        # Trailing piece.
        if last < len(text):
            pieces.append((text[last:], last, len(text)))

        # Merge false-positive splits after abbreviations.
        merged: list[tuple[str, int, int]] = []
        for seg, start, end in pieces:
            if merged and self._ends_with_abbreviation(merged[-1][0]):
                prev_seg, prev_start, _ = merged[-1]
                merged[-1] = (prev_seg + " " + seg, prev_start, end)
            else:
                merged.append((seg, start, end))
        return merged

    @staticmethod
    def _ends_with_abbreviation(segment: str) -> bool:
        token = segment.strip().split()
        if not token:
            return False
        return token[-1].lower() in _ABBREVIATIONS

    def _is_factual(self, text: str) -> bool:
        """Heuristic: keep fragments that look like factual statements."""
        if len(text) < self.min_claim_length:
            return False
        lowered = text.lower()
        if any(lowered.startswith(p) for p in _NON_FACTUAL_PREFIXES):
            return False
        # Pure questions are not factual claims.
        if text.endswith("?") and not text.rstrip("?").strip().endswith("."):
            # A trailing question mark with no declarative content.
            if "?" in text and text.count(".") == 0:
                return False
        # Must contain at least one alphabetic character.
        if not any(ch.isalpha() for ch in text):
            return False
        return True

    @staticmethod
    def _citations_for_span(
        span: tuple[int, int],
        citations: list[Citation],
    ) -> list[str]:
        """Return citation ids whose answer_span overlaps ``span``."""
        start, end = span
        hits: list[str] = []
        for c in citations:
            if c.answer_span is None:
                continue
            c_start, c_end = c.answer_span
            # Overlap test.
            if c_start < end and c_end > start:
                hits.append(c.citation_id)
        return hits
