"""
evidence_matcher.py
===================

Match each extracted claim against the retrieved evidence set and assign a
support level.

WHY THIS FILE EXISTS
--------------------
A claim is only as trustworthy as the evidence behind it. This module answers,
for every claim: *"Which retrieved passage best supports this, and how
strongly?"* Its output (EvidenceMatch objects + per-claim best support level)
is the raw material for citation validation, hallucination detection, and
confidence scoring.

RESPONSIBILITIES
----------------
* For each claim, score similarity against every candidate evidence passage.
* Convert similarity into a SupportLevel (FULL / PARTIAL / NO support) using
  configurable thresholds.
* Prefer evidence that the generator already cited (the claim's
  supporting_citations) when scores are close — this rewards honest citation.
* Return both the per-claim best match and the full list of matches for audit.

INPUTS
------
* claims: list[Claim]
* evidence: list[Evidence]

OUTPUTS
-------
* matches: list[EvidenceMatch]            (every recorded match)
* best_by_claim: dict[claim_id, EvidenceMatch]

DESIGN DECISIONS
----------------
* Similarity uses a lexical token-overlap measure (a weighted Jaccard /
  containment hybrid). This is deterministic, explainable, and dependency-free
  — three properties auditors care about far more than a marginal accuracy gain
  from an opaque embedding model. The scoring function is isolated so it can be
  swapped for an embedding-based scorer later without touching callers.
* Empty evidence is surfaced via EmptyEvidenceError so the *pipeline* decides
  policy (reject vs degrade), rather than silently returning NO_SUPPORT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .errors import EmptyEvidenceError, EvidenceMatchingError
from .grounding_result import Claim, Evidence, EvidenceMatch, SupportLevel


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Very common words contribute little discriminating signal.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "was", "were", "is", "are", "be", "been", "being",
    "by", "as", "that", "this", "it", "its", "from", "has", "have", "had",
    "will", "would", "can", "could", "should", "do", "does", "did",
}


@dataclass(frozen=True)
class MatcherThresholds:
    """Tunable thresholds controlling support-level assignment."""

    full_support: float = 0.6
    partial_support: float = 0.3

    def level_for(self, score: float) -> SupportLevel:
        if score >= self.full_support:
            return SupportLevel.FULL_SUPPORT
        if score >= self.partial_support:
            return SupportLevel.PARTIAL_SUPPORT
        return SupportLevel.NO_SUPPORT


class EvidenceMatcher:
    """Scores claims against evidence and assigns support levels."""

    def __init__(
        self,
        thresholds: Optional[MatcherThresholds] = None,
        cited_bonus: float = 0.05,
    ) -> None:
        self.thresholds = thresholds or MatcherThresholds()
        # Small bonus when the matched evidence was actually cited by the
        # generator for this claim. Rewards honest citation without letting a
        # bogus citation manufacture support on its own.
        self.cited_bonus = cited_bonus

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def match(
        self,
        claims: list[Claim],
        evidence: list[Evidence],
    ) -> tuple[list[EvidenceMatch], dict[str, EvidenceMatch]]:
        """Return (all_matches, best_match_by_claim_id).

        Raises
        ------
        EmptyEvidenceError
            If ``evidence`` is empty.
        EvidenceMatchingError
            On unexpected scoring failure.
        """
        if not evidence:
            raise EmptyEvidenceError(
                "No evidence available to match claims against.",
                details={"claim_count": len(claims)},
            )

        try:
            # Pre-tokenise evidence once.
            evidence_tokens = {
                ev.evidence_id: self._tokenise(ev.text) for ev in evidence
            }
            evidence_by_id = {ev.evidence_id: ev for ev in evidence}

            all_matches: list[EvidenceMatch] = []
            best_by_claim: dict[str, EvidenceMatch] = {}

            for claim in claims:
                claim_tokens = self._tokenise(claim.text)
                cited = set(claim.supporting_citations)
                best: Optional[EvidenceMatch] = None

                for ev in evidence:
                    raw = self._similarity(claim_tokens, evidence_tokens[ev.evidence_id])
                    # Reward evidence the generator cited for this claim.
                    score = raw
                    if cited and ev.evidence_id in self._cited_evidence_ids(claim, evidence_by_id):
                        score = min(1.0, raw + self.cited_bonus)

                    level = self.thresholds.level_for(score)
                    match = EvidenceMatch(
                        claim_id=claim.claim_id,
                        evidence_id=ev.evidence_id,
                        similarity_score=round(score, 4),
                        support_level=level,
                    )
                    # Only record meaningful matches in the audit trail to keep
                    # it readable, but always keep the best one even if NO_SUPPORT.
                    if level is not SupportLevel.NO_SUPPORT:
                        all_matches.append(match)

                    if best is None or match.similarity_score > best.similarity_score:
                        best = match

                if best is not None:
                    best_by_claim[claim.claim_id] = best
                    # Ensure the best match is in the audit list even if NO_SUPPORT.
                    if best.support_level is SupportLevel.NO_SUPPORT:
                        all_matches.append(best)

            return all_matches, best_by_claim

        except EmptyEvidenceError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise EvidenceMatchingError(
                "Unexpected failure during evidence matching.",
                details={"cause": str(exc)},
            ) from exc

    # ------------------------------------------------------------------ #
    # Scoring internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tokenise(text: str) -> set[str]:
        tokens = _TOKEN_RE.findall(text.lower())
        return {t for t in tokens if t not in _STOPWORDS}

    @staticmethod
    def _similarity(claim_tokens: set[str], evidence_tokens: set[str]) -> float:
        """Hybrid containment / Jaccard similarity in [0, 1].

        We weight *containment* (how much of the claim is covered by the
        evidence) more heavily than Jaccard, because a long evidence passage
        that fully contains a short claim should score high even though the
        Jaccard overlap is diluted by the passage length.
        """
        if not claim_tokens:
            return 0.0
        if not evidence_tokens:
            return 0.0
        intersection = claim_tokens & evidence_tokens
        if not intersection:
            return 0.0
        containment = len(intersection) / len(claim_tokens)
        union = claim_tokens | evidence_tokens
        jaccard = len(intersection) / len(union)
        # 70% containment, 30% jaccard: containment dominates because a long
        # evidence passage fully covering a short claim is strong support.
        blended = 0.7 * containment + 0.3 * jaccard
        return round(blended, 4)

    @staticmethod
    def _cited_evidence_ids(
        claim: Claim,
        evidence_by_id: dict[str, Evidence],
    ) -> set[str]:
        """Resolve a claim's citation ids to evidence ids.

        In this slice citation_id and evidence_id are linked via the Citation
        objects upstream; the claim only stores citation_ids. We treat a
        citation id as referencing evidence directly when it matches an
        evidence id, which is the common convention in the generation layer.
        Unknown ids are ignored (defensive).
        """
        return {cid for cid in claim.supporting_citations if cid in evidence_by_id}
