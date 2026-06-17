"""
grounding_pipeline.py
=====================

The orchestrator for the Grounding & Answer Verification slice (S1.9).

WHY THIS FILE EXISTS
--------------------
Every other module does one job. This file wires them into the end-to-end flow
that turns a *generated answer + retrieved evidence* into a *verified, scored,
audited GroundingResult*. It is the single public entry-point the generation /
API layer calls.

PIPELINE STAGES
---------------
    1. Extract claims from the answer.                (claim_extractor)
    2. Match each claim to evidence.                  (evidence_matcher)
    3. Validate citations / compute coverage.         (citation_validator)
    4. Detect hallucination risk.                     (hallucination_detector)
    5. Score confidence.                              (confidence_scorer)
    6. Decide verification status.                    (answer_verifier)
    7. Assemble GroundingResult.                      (grounding_result)
    8. Build + persist audit record.                  (audit_trail)
    9. Record metrics.                                (metrics)

INPUTS  (GroundingRequest)
--------------------------
* request_id, answer_id
* answer_text
* evidence:    list[Evidence]   (from backend/context/)
* citations:   list[Citation]   (from backend/generation/)
* retrieval_relevance: optional override; otherwise derived from evidence.

OUTPUT
------
* GroundingResult

INTERACTION WITH OTHER SLICES
-----------------------------
* Consumes backend/context/ retrieval output as ``Evidence``.
* Consumes backend/generation/ output as ``answer_text`` + ``Citation`` list.
* Emits a GroundingResult the API layer uses to gate / annotate the response.

FAILURE HANDLING / GRACEFUL DEGRADATION
---------------------------------------
* Empty evidence ⇒ no matching possible ⇒ degrade to a REJECTED result with a
  warning rather than crashing.
* Malformed answer ⇒ REJECTED result with a warning.
* Verification timeout ⇒ REJECTED result with a warning (the deadline is a soft
  wall-clock budget checked between stages).
* Audit write failure ⇒ result is still returned; a warning is attached.
* Confidence computation failure ⇒ confidence forced to 0.0 with a warning and
  risk treated as HIGH (fail safe, not fail open).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .answer_verifier import AnswerVerifier, VerificationPolicy
from .audit_trail import AuditTrail
from .citation_validator import CitationValidator
from .claim_extractor import ClaimExtractor
from .confidence_scorer import ConfidenceScorer
from .errors import (
    AuditWriteError,
    ConfidenceComputationError,
    EmptyEvidenceError,
    GroundingError,
    MalformedAnswerError,
    VerificationTimeoutError,
)
from .evidence_matcher import EvidenceMatcher
from .grounding_result import (
    Citation,
    Claim,
    Evidence,
    EvidenceMatch,
    GroundingResult,
    HallucinationRisk,
    SupportLevel,
    VerificationStatus,
)
from .hallucination_detector import HallucinationDetector
from .metrics import GroundingMetrics


@dataclass
class GroundingRequest:
    """Input envelope for the pipeline."""

    request_id: str
    answer_id: str
    answer_text: str
    evidence: list[Evidence] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    # Optional explicit retrieval relevance (0..1). If None it is derived from
    # the mean relevance of the evidence set.
    retrieval_relevance: Optional[float] = None


class GroundingPipeline:
    """Coordinates the full grounding & verification flow."""

    def __init__(
        self,
        *,
        claim_extractor: Optional[ClaimExtractor] = None,
        evidence_matcher: Optional[EvidenceMatcher] = None,
        citation_validator: Optional[CitationValidator] = None,
        hallucination_detector: Optional[HallucinationDetector] = None,
        confidence_scorer: Optional[ConfidenceScorer] = None,
        answer_verifier: Optional[AnswerVerifier] = None,
        audit_trail: Optional[AuditTrail] = None,
        metrics: Optional[GroundingMetrics] = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.evidence_matcher = evidence_matcher or EvidenceMatcher()
        self.citation_validator = citation_validator or CitationValidator()
        self.hallucination_detector = (
            hallucination_detector or HallucinationDetector()
        )
        self.confidence_scorer = confidence_scorer or ConfidenceScorer()
        self.answer_verifier = answer_verifier or AnswerVerifier()
        self.audit_trail = audit_trail or AuditTrail()
        self.metrics = metrics or GroundingMetrics()
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self, request: GroundingRequest) -> GroundingResult:
        """Execute the full pipeline and always return a GroundingResult."""
        started = time.monotonic()
        warnings: list[str] = []

        try:
            result = self._run_inner(request, started, warnings)
        except MalformedAnswerError as exc:
            result = self._degraded_result(
                request, warnings + [f"Malformed answer: {exc.message}"]
            )
        except EmptyEvidenceError as exc:
            result = self._degraded_result(
                request, warnings + [f"No evidence available: {exc.message}"]
            )
        except VerificationTimeoutError as exc:
            result = self._degraded_result(
                request, warnings + [f"Verification timed out: {exc.message}"]
            )
        except GroundingError as exc:
            # Any other typed grounding failure: fail safe.
            result = self._degraded_result(
                request, warnings + [f"Grounding error: {exc.message}"]
            )

        latency = time.monotonic() - started
        self._record_metrics(result, latency)
        return result

    # ------------------------------------------------------------------ #
    # Core flow
    # ------------------------------------------------------------------ #
    def _run_inner(
        self,
        request: GroundingRequest,
        started: float,
        warnings: list[str],
    ) -> GroundingResult:
        # Stage 1: extract claims.
        claims = self.claim_extractor.extract(
            request.answer_text, request.answer_id, request.citations
        )
        self._check_deadline(started)

        # Stage 2: match evidence. (raises EmptyEvidenceError if no evidence)
        all_matches, best_by_claim = self.evidence_matcher.match(
            claims, request.evidence
        )
        self._check_deadline(started)

        evidence_ids = {ev.evidence_id for ev in request.evidence}

        # Stage 3: citation validation / coverage.
        citation_report = self.citation_validator.validate(
            claims, best_by_claim, evidence_ids
        )
        warnings.extend(citation_report.warnings)
        self._check_deadline(started)

        # Stage 4: hallucination detection.
        halluc_report = self.hallucination_detector.detect(
            claims, best_by_claim, citation_report.citation_coverage
        )
        self._check_deadline(started)

        # Stage 5: confidence scoring (fail safe on error).
        retrieval_relevance = self._retrieval_relevance(request)
        avg_support = self._avg_support_weight(claims, best_by_claim)
        try:
            confidence = self.confidence_scorer.score(
                retrieval_relevance=retrieval_relevance,
                citation_coverage=citation_report.citation_coverage,
                avg_support_weight=avg_support,
                hallucination_risk=halluc_report.risk,
            )
            confidence_score = confidence.score
        except ConfidenceComputationError as exc:
            warnings.append(
                f"Confidence computation failed; forcing 0.0: {exc.message}"
            )
            confidence_score = 0.0
            halluc_report.risk = HallucinationRisk.HIGH  # fail safe

        # Stage 6: verification decision.
        decision = self.answer_verifier.verify(
            unsupported_count=len(halluc_report.unsupported_claim_ids),
            hallucination_risk=halluc_report.risk,
            confidence_score=confidence_score,
        )
        warnings.append(decision.rationale)

        # Stage 7: assemble result.
        unsupported_set = set(halluc_report.unsupported_claim_ids)
        verified_claims = [c for c in claims if c.claim_id not in unsupported_set]
        unsupported_claims = [c for c in claims if c.claim_id in unsupported_set]

        result = GroundingResult(
            answer_id=request.answer_id,
            verified_claims=verified_claims,
            unsupported_claims=unsupported_claims,
            confidence_score=confidence_score,
            hallucination_risk=halluc_report.risk,
            citation_coverage=citation_report.citation_coverage,
            verification_status=decision.status,
            evidence_matches=all_matches,
            warnings=warnings,
        )

        # Stage 8: audit (graceful on failure).
        self._audit(request, result, warnings)

        return result

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _check_deadline(self, started: float) -> None:
        if time.monotonic() - started > self.timeout_seconds:
            raise VerificationTimeoutError(
                "Verification exceeded the configured deadline.",
                details={"timeout_seconds": self.timeout_seconds},
            )

    @staticmethod
    def _retrieval_relevance(request: GroundingRequest) -> float:
        if request.retrieval_relevance is not None:
            return request.retrieval_relevance
        if not request.evidence:
            return 0.0
        scores = [ev.relevance_score for ev in request.evidence]
        return round(sum(scores) / len(scores), 4)

    @staticmethod
    def _avg_support_weight(
        claims: list[Claim], best_by_claim: dict[str, EvidenceMatch]
    ) -> float:
        if not claims:
            return 1.0  # nothing to support ⇒ neutral-high
        total = 0.0
        for c in claims:
            best = best_by_claim.get(c.claim_id)
            total += best.support_level.weight if best else 0.0
        return round(total / len(claims), 4)

    def _audit(
        self,
        request: GroundingRequest,
        result: GroundingResult,
        warnings: list[str],
    ) -> None:
        try:
            record = self.audit_trail.build_record(
                request_id=request.request_id,
                result=result,
                retrieval_ids=[ev.evidence_id for ev in request.evidence],
                citation_ids=[c.citation_id for c in request.citations],
            )
            self.audit_trail.persist(record)
        except AuditWriteError as exc:
            warnings.append(f"Audit write failed: {exc.message}")

    def _degraded_result(
        self, request: GroundingRequest, warnings: list[str]
    ) -> GroundingResult:
        """Build a fail-safe REJECTED result and still attempt to audit it."""
        result = GroundingResult(
            answer_id=request.answer_id,
            verified_claims=[],
            unsupported_claims=[],
            confidence_score=0.0,
            hallucination_risk=HallucinationRisk.HIGH,
            citation_coverage=0.0,
            verification_status=VerificationStatus.REJECTED,
            evidence_matches=[],
            warnings=warnings,
        )
        self._audit(request, result, warnings)
        return result

    def _record_metrics(self, result: GroundingResult, latency: float) -> None:
        self.metrics.record(
            status=result.verification_status,
            hallucination_risk=result.hallucination_risk,
            citation_coverage=result.citation_coverage,
            confidence_score=result.confidence_score,
            latency_seconds=latency,
        )
