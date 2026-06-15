"""Lightweight, dependency-free text utilities.

Slice 1.3 deliberately avoids heavy NLP dependencies (spaCy/NLTK). These
helpers provide deterministic sentence segmentation and naive noun-phrase
detection sufficient for the rule-based and heuristic stages. They can be
swapped for a real NLP backend in a later slice without changing extractor
interfaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Sentence boundary: terminal punctuation followed by whitespace + capital/quote/digit.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])")

# A very small stop-word set used to trim candidate noun phrases.
_LEADING_STOPWORDS = {
    "the", "a", "an", "this", "that", "these", "those", "our", "their",
    "his", "her", "its", "my", "your",
}


@dataclass(frozen=True)
class Sentence:
    """A sentence with its absolute character offsets in the source text."""

    text: str
    start: int
    end: int


def segment_sentences(text: str) -> List[Sentence]:
    """Split ``text`` into sentences while preserving source offsets.

    Returns an empty list for empty/whitespace-only input.
    """
    if not text or not text.strip():
        return []

    sentences: List[Sentence] = []
    cursor = 0
    # Split on boundaries but keep track of offsets by re-locating each piece.
    pieces = _SENTENCE_BOUNDARY.split(text)
    for piece in pieces:
        stripped = piece.strip()
        if not stripped:
            continue
        idx = text.find(stripped, cursor)
        if idx < 0:
            idx = cursor
        start = idx
        end = idx + len(stripped)
        cursor = end
        sentences.append(Sentence(text=stripped, start=start, end=end))
    return sentences


def normalize_phrase(phrase: str) -> str:
    """Trim whitespace and a single leading article/determiner."""
    cleaned = " ".join(phrase.strip().split())
    if not cleaned:
        return cleaned
    tokens = cleaned.split(" ")
    if tokens[0].lower() in _LEADING_STOPWORDS and len(tokens) > 1:
        tokens = tokens[1:]
    return " ".join(tokens)


def looks_like_noun_phrase(phrase: str) -> bool:
    """Heuristic: a candidate is a noun phrase if it contains at least one
    capitalized token or is a short (<=4 token) lowercase span."""
    cleaned = phrase.strip()
    if not cleaned:
        return False
    tokens = cleaned.split()
    if any(t[:1].isupper() for t in tokens):
        return True
    return 1 <= len(tokens) <= 4
