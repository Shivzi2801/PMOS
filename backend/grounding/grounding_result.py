"""
grounding_result.py
===================

Core data contracts for the Grounding & Answer Verification slice (S1.9).

WHY THIS FILE EXISTS
--------------------
Every other module in this slice (extraction, matching, scoring, verification,
auditing) speaks in terms of a small set of shared, immutable data structures.
Centralising them here means:

  * one source of truth for the shape of grounding data,
  * no circular imports between processing modules,
  * trivial serialisation to/from JSON for the audit trail and the API layer.

These dataclasses are deliberately *pure data* — they carry no behaviour beyond
validation and (de)serialisation. All logic lives in the processing modules.

CONTRACTS DEFINED HERE
----------------------
* SupportLevel           - enum: how strongly evidence supports a claim
* HallucinationRisk      - enum: LOW / MEDIUM / HIGH
* VerificationStatus     - enum: VERIFIED / PARTIALLY_VERIFIED / REJECTED
* Citation               - a reference emitted by the generation layer
* Evidence               - a retrieved passage from the context layer
* Claim                  - an atomic, independently verifiable statement
* EvidenceMatch          - link between a claim and a piece of evidence
* GroundingResult        - the final verified output of the pipeline
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class SupportLevel(str, enum.Enum):
    """How strongly a single piece of evidence supports a single claim."""

    FULL_SUPPORT = "FULL_SUPPORT"
    PARTIAL_SUPPORT = "PARTIAL_SUPPORT"
    NO_SUPPORT = "NO_SUPPORT"

    @property
    def weight(self) -> float:
        """Numeric weight used by the confidence scorer."""
        return {
            SupportLevel.FULL_SUPPORT: 1.0,
            SupportLevel.PARTIAL_SUPPORT: 0.5,
            SupportLevel.NO_SUPPORT: 0.0,
        }[self]


class HallucinationRisk(str, enum.Enum):
    """Coarse risk band for how likely an answer contains fabrication."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class VerificationStatus(str, enum.Enum):
    """Final verdict on an answer."""

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    REJECTED = "REJECTED"


# --------------------------------------------------------------------------- #
# Inputs (produced by upstream slices)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Citation:
    """A citation emitted by the generation layer (backend/generation/).

    A citation is a *claim by the generator* that a particular piece of
    retrieved evidence supports part of the answer. The grounding layer does
    NOT trust citations blindly — it re-validates them against evidence.
    """

    citation_id: str
    evidence_id: str
    # Optional span in the answer text this citation is attached to.
    answer_span: Optional[tuple[int, int]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "evidence_id": self.evidence_id,
            "answer_span": list(self.answer_span) if self.answer_span else None,
        }


@dataclass(frozen=True)
class Evidence:
    """A retrieved passage from the context layer (backend/context/).

    Attributes
    ----------
    evidence_id:
        Stable id used to trace this passage back through retrieval.
    text:
        The passage content used for matching.
    relevance_score:
        Retrieval relevance (0..1) carried over from the context slice.
        Feeds the confidence scorer.
    source:
        Optional human-readable origin (document name, url, ticket id).
    """

    evidence_id: str
    text: str
    relevance_score: float = 0.0
    source: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "text": self.text,
            "relevance_score": self.relevance_score,
            "source": self.source,
        }


# --------------------------------------------------------------------------- #
# Intermediate structures
# --------------------------------------------------------------------------- #
@dataclass
class Claim:
    """An atomic, independently verifiable statement extracted from an answer.

    Attributes
    ----------
    claim_id:
        Stable id (deterministic within a single answer).
    text:
        The claim sentence/fragment.
    source_span:
        (start, end) character offsets into the original answer text. Enables
        UI highlighting and precise audit replay.
    supporting_citations:
        Citation ids the generator attached to the answer span this claim came
        from. Used as *hints* for matching, never as proof.
    """

    claim_id: str
    text: str
    source_span: tuple[int, int]
    supporting_citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "source_span": list(self.source_span),
            "supporting_citations": list(self.supporting_citations),
        }


@dataclass
class EvidenceMatch:
    """Link between one claim and one piece of evidence.

    The matcher emits one of these per (claim, candidate-evidence) pair it
    considers worth recording. The *best* match per claim drives the claim's
    support level.
    """

    claim_id: str
    evidence_id: str
    similarity_score: float
    support_level: SupportLevel

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "evidence_id": self.evidence_id,
            "similarity_score": self.similarity_score,
            "support_level": self.support_level.value,
        }


# --------------------------------------------------------------------------- #
# Final output
# --------------------------------------------------------------------------- #
@dataclass
class GroundingResult:
    """The final, auditable output of the grounding pipeline.

    This is what the API / generation layer consumes to decide what to show the
    user and with what trust indicators.
    """

    answer_id: str
    verified_claims: list[Claim]
    unsupported_claims: list[Claim]
    confidence_score: float
    hallucination_risk: HallucinationRisk
    citation_coverage: float  # 0..1
    verification_status: VerificationStatus
    # Full traceability: every match the pipeline relied on.
    evidence_matches: list[EvidenceMatch] = field(default_factory=list)
    # Non-fatal problems surfaced during processing (degraded mode, etc.)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_claims(self) -> int:
        return len(self.verified_claims) + len(self.unsupported_claims)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_id": self.answer_id,
            "verified_claims": [c.to_dict() for c in self.verified_claims],
            "unsupported_claims": [c.to_dict() for c in self.unsupported_claims],
            "confidence_score": self.confidence_score,
            "hallucination_risk": self.hallucination_risk.value,
            "citation_coverage": self.citation_coverage,
            "verification_status": self.verification_status.value,
            "evidence_matches": [m.to_dict() for m in self.evidence_matches],
            "warnings": list(self.warnings),
            "total_claims": self.total_claims,
        }
