"""
backend.grounding
=================

Slice S1.9 — Grounding & Answer Verification.

This package validates generated answers against retrieved evidence, verifies
citation coverage, detects unsupported claims, computes confidence scores,
produces audit records, and flags hallucination risk.

PUBLIC ENTRY-POINT
------------------
Construct a :class:`GroundingPipeline` and call :meth:`run` with a
:class:`GroundingRequest`:

    from backend.grounding import GroundingPipeline, GroundingRequest, Evidence, Citation

    pipeline = GroundingPipeline()
    result = pipeline.run(
        GroundingRequest(
            request_id="req-1",
            answer_id="ans-1",
            answer_text="The refund was approved on May 6.",
            evidence=[Evidence("e1", "Refund approved on May 6.", relevance_score=0.9)],
            citations=[Citation("e1", "e1")],
        )
    )
    print(result.verification_status, result.confidence_score)
"""

from __future__ import annotations

# Contracts
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

# Processing components
from .answer_verifier import AnswerVerifier, VerificationDecision, VerificationPolicy
from .audit_trail import (
    AuditRecord,
    AuditSink,
    AuditTrail,
    InMemoryAuditSink,
    JsonlFileAuditSink,
)
from .citation_validator import CitationFinding, CitationReport, CitationValidator
from .claim_extractor import ClaimExtractor
from .confidence_scorer import (
    ConfidenceBreakdown,
    ConfidenceScorer,
    ConfidenceWeights,
)
from .evidence_matcher import EvidenceMatcher, MatcherThresholds
from .hallucination_detector import (
    HallucinationDetector,
    HallucinationReport,
    HallucinationThresholds,
)
from .metrics import GroundingMetrics

# Orchestrator
from .grounding_pipeline import GroundingPipeline, GroundingRequest

# Errors
from .errors import (
    AuditWriteError,
    ClaimExtractionError,
    ConfidenceComputationError,
    EmptyEvidenceError,
    EvidenceMatchingError,
    GroundingError,
    MalformedAnswerError,
    MalformedClaimError,
    MissingCitationsError,
    VerificationTimeoutError,
)

__all__ = [
    # contracts
    "Citation",
    "Claim",
    "Evidence",
    "EvidenceMatch",
    "GroundingResult",
    "HallucinationRisk",
    "SupportLevel",
    "VerificationStatus",
    # components
    "AnswerVerifier",
    "VerificationDecision",
    "VerificationPolicy",
    "AuditRecord",
    "AuditSink",
    "AuditTrail",
    "InMemoryAuditSink",
    "JsonlFileAuditSink",
    "CitationFinding",
    "CitationReport",
    "CitationValidator",
    "ClaimExtractor",
    "ConfidenceBreakdown",
    "ConfidenceScorer",
    "ConfidenceWeights",
    "EvidenceMatcher",
    "MatcherThresholds",
    "HallucinationDetector",
    "HallucinationReport",
    "HallucinationThresholds",
    "GroundingMetrics",
    # orchestrator
    "GroundingPipeline",
    "GroundingRequest",
    # errors
    "AuditWriteError",
    "ClaimExtractionError",
    "ConfidenceComputationError",
    "EmptyEvidenceError",
    "EvidenceMatchingError",
    "GroundingError",
    "MalformedAnswerError",
    "MalformedClaimError",
    "MissingCitationsError",
    "VerificationTimeoutError",
]
