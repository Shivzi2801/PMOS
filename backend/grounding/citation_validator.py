"""
citation_validator.py
=====================

Validate citations and compute citation coverage.

WHY THIS FILE EXISTS
--------------------
Enterprise trust hinges on a simple promise: *every factual claim is backed by
evidence the user can inspect.* This module turns that promise into a number —
``citation_coverage`` — and a set of structured validation findings.

It does NOT trust the generator's citations at face value. A citation is only
"valid" if the matcher actually found supporting evidence for the claim it is
attached to. This catches the common failure where a model cites a real
document that does not, in fact, support the sentence.

RESPONSIBILITIES
----------------
* Determine, per claim, whether it is "supported" (FULL or PARTIAL) or not.
* Compute citation_coverage = supported_claims / total_claims.
* Detect dangling citations (cite an evidence id that does not exist).
* Detect uncited-but-supported and cited-but-unsupported claims.

INPUTS
------
* claims: list[Claim]
* best_by_claim: dict[claim_id, EvidenceMatch]
* evidence_ids: set[str]   (valid evidence ids for dangling-citation checks)

OUTPUTS
-------
* CitationReport with coverage + findings.

DESIGN DECISIONS
----------------
* PARTIAL_SUPPORT counts as "supported" for coverage but is recorded as a
  finding, because partial support still warrants reviewer attention.
* Coverage of an answer with zero claims is defined as 1.0 (nothing to ground
  ⇒ trivially fully covered) but flagged with a warning so callers can decide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .grounding_result import Claim, EvidenceMatch, SupportLevel


@dataclass
class CitationFinding:
    """A single notable observation about citations for one claim."""

    claim_id: str
    kind: str  # e.g. "CITED_BUT_UNSUPPORTED", "DANGLING_CITATION", "PARTIAL"
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"claim_id": self.claim_id, "kind": self.kind, "message": self.message}


@dataclass
class CitationReport:
    """Result of citation validation."""

    citation_coverage: float
    supported_claim_ids: list[str]
    unsupported_claim_ids: list[str]
    findings: list[CitationFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_coverage": self.citation_coverage,
            "supported_claim_ids": list(self.supported_claim_ids),
            "unsupported_claim_ids": list(self.unsupported_claim_ids),
            "findings": [f.to_dict() for f in self.findings],
            "warnings": list(self.warnings),
        }


class CitationValidator:
    """Validates citations and computes coverage."""

    # Support levels that count as "the claim is grounded".
    _SUPPORTED = {SupportLevel.FULL_SUPPORT, SupportLevel.PARTIAL_SUPPORT}

    def validate(
        self,
        claims: list[Claim],
        best_by_claim: dict[str, EvidenceMatch],
        evidence_ids: set[str],
    ) -> CitationReport:
        supported: list[str] = []
        unsupported: list[str] = []
        findings: list[CitationFinding] = []
        warnings: list[str] = []

        for claim in claims:
            best = best_by_claim.get(claim.claim_id)
            level = best.support_level if best else SupportLevel.NO_SUPPORT

            if level in self._SUPPORTED:
                supported.append(claim.claim_id)
                if level is SupportLevel.PARTIAL_SUPPORT:
                    findings.append(
                        CitationFinding(
                            claim_id=claim.claim_id,
                            kind="PARTIAL",
                            message=(
                                "Claim is only partially supported by the best "
                                "matching evidence; reviewer attention advised."
                            ),
                        )
                    )
            else:
                unsupported.append(claim.claim_id)
                if claim.supporting_citations:
                    # The generator claimed support but matching disagreed.
                    findings.append(
                        CitationFinding(
                            claim_id=claim.claim_id,
                            kind="CITED_BUT_UNSUPPORTED",
                            message=(
                                "Generator attached citations but no evidence "
                                "sufficiently supports the claim."
                            ),
                        )
                    )

            # Dangling citation detection.
            for cid in claim.supporting_citations:
                if cid not in evidence_ids:
                    findings.append(
                        CitationFinding(
                            claim_id=claim.claim_id,
                            kind="DANGLING_CITATION",
                            message=(
                                f"Citation '{cid}' does not reference any known "
                                "evidence id."
                            ),
                        )
                    )

            # Honest-but-uncited: supported without the generator citing anything.
            if level in self._SUPPORTED and not claim.supporting_citations:
                findings.append(
                    CitationFinding(
                        claim_id=claim.claim_id,
                        kind="SUPPORTED_BUT_UNCITED",
                        message=(
                            "Claim is supported by evidence but the generator "
                            "provided no citation; coverage credited, citation "
                            "hygiene flagged."
                        ),
                    )
                )

        total = len(claims)
        if total == 0:
            coverage = 1.0
            warnings.append("Answer contained no factual claims to ground.")
        else:
            coverage = round(len(supported) / total, 4)

        return CitationReport(
            citation_coverage=coverage,
            supported_claim_ids=supported,
            unsupported_claim_ids=unsupported,
            findings=findings,
            warnings=warnings,
        )
