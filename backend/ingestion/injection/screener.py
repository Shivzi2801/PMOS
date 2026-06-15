"""Injection screener — scores text against the pattern catalogue."""

from __future__ import annotations

import re
from typing import List, Optional

from ..contracts import (
    InjectionFinding,
    InjectionMatch,
    InjectionStatus,
    InjectionCategory,
)
from .patterns import InjectionPattern, DEFAULT_PATTERNS

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")


def _preview(text: str, start: int, end: int, ctx: int = 24) -> str:
    lo = max(0, start - ctx)
    hi = min(len(text), end + ctx)
    snippet = text[lo:hi].replace("\n", " ").strip()
    return ("..." if lo > 0 else "") + snippet + ("..." if hi < len(text) else "")


class InjectionScreener:
    """Maps a cumulative risk score to SAFE / SUSPECT / QUARANTINED.

    Default thresholds:
        score < suspect_threshold              -> SAFE
        suspect_threshold <= score < quarantine_threshold -> SUSPECT
        score >= quarantine_threshold          -> QUARANTINED
    """

    def __init__(
        self,
        patterns: Optional[List[InjectionPattern]] = None,
        *,
        suspect_threshold: float = 0.45,
        quarantine_threshold: float = 0.85,
        hidden_char_weight: float = 0.5,
    ) -> None:
        self._patterns = patterns if patterns is not None else list(DEFAULT_PATTERNS)
        self._suspect_threshold = suspect_threshold
        self._quarantine_threshold = quarantine_threshold
        self._hidden_char_weight = hidden_char_weight

    def screen(self, text: str) -> InjectionFinding:
        if not text:
            return InjectionFinding(status=InjectionStatus.SAFE, score=0.0, matches=[])

        matches: List[InjectionMatch] = []

        # Detect zero-width / invisible characters on the RAW text.
        for m in _ZERO_WIDTH_RE.finditer(text):
            matches.append(
                InjectionMatch(
                    category=InjectionCategory.HIDDEN_PROMPT,
                    pattern_name="zero_width_char",
                    weight=self._hidden_char_weight,
                    start=m.start(),
                    end=m.end(),
                    preview="<zero-width char>",
                )
            )

        for pat in self._patterns:
            for m in pat.regex.finditer(text):
                matches.append(
                    InjectionMatch(
                        category=pat.category,
                        pattern_name=pat.name,
                        weight=pat.weight,
                        start=m.start(),
                        end=m.end(),
                        preview=_preview(text, m.start(), m.end()),
                    )
                )

        score = self._score(matches)
        status = self._status(score)
        return InjectionFinding(status=status, score=round(score, 4), matches=matches)

    def _score(self, matches: List[InjectionMatch]) -> float:
        """Sum weights, with a diversity bonus for multiple distinct categories.

        Hitting several different attack families is a much stronger signal than
        repeating one pattern, so distinct categories add a small bonus while
        repeated hits of the same pattern contribute with diminishing returns.
        """
        if not matches:
            return 0.0

        by_pattern: dict[str, int] = {}
        base = 0.0
        for m in matches:
            n = by_pattern.get(m.pattern_name, 0)
            base += m.weight * (1.0 if n == 0 else 0.3)  # diminishing repeats
            by_pattern[m.pattern_name] = n + 1

        distinct_categories = len({m.category for m in matches})
        diversity_bonus = 0.15 * max(0, distinct_categories - 1)
        return base + diversity_bonus

    def _status(self, score: float) -> InjectionStatus:
        if score >= self._quarantine_threshold:
            return InjectionStatus.QUARANTINED
        if score >= self._suspect_threshold:
            return InjectionStatus.SUSPECT
        return InjectionStatus.SAFE
