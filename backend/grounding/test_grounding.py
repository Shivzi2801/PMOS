"""
test_grounding.py
=================

Test suite for Slice S1.9 — Grounding & Answer Verification.

Run with:  pytest -q backend/grounding/test_grounding.py
       or:  python -m pytest -q

Coverage map (mirrors the slice's required test list):
  * fully grounded answers
  * partially grounded answers
  * hallucinated answers
  * citation validation
  * confidence scoring
  * audit generation
  * verification status transitions
  * failure handling / graceful degradation
"""

from __future__ import annotations

import pytest

from backend.grounding import (
    AnswerVerifier,
    AuditTrail,
    Citation,
    CitationValidator,
    ClaimExtractor,
    ConfidenceScorer,
    ConfidenceWeights,
    Evidence,
    EvidenceMatcher,
    GroundingMetrics,
    GroundingPipeline,
    GroundingRequest,
    HallucinationDetector,
    HallucinationRisk,
    InMemoryAuditSink,
    SupportLevel,
    VerificationStatus,
)
from backend.grounding.confidence_scorer import ConfidenceComputationError
from backend.grounding.errors import MalformedAnswerError


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def make_pipeline(**kwargs) -> tuple[GroundingPipeline, InMemoryAuditSink, GroundingMetrics]:
    sink = InMemoryAuditSink()
    metrics = GroundingMetrics()
    pipeline = GroundingPipeline(
        audit_trail=AuditTrail(sink=sink),
        metrics=metrics,
        **kwargs,
    )
    return pipeline, sink, metrics


# --------------------------------------------------------------------------- #
# Claim extraction
# --------------------------------------------------------------------------- #
def test_claim_extraction_splits_into_atomic_claims():
    extractor = ClaimExtractor()
    text = (
        "The customer requested a refund on May 4. "
        "The refund was approved on May 6."
    )
    claims = extractor.extract(text, "ans-1")
    assert len(claims) == 2
    assert "requested a refund" in claims[0].text
    assert "approved" in claims[1].text
    # Deterministic ids.
    assert claims[0].claim_id == "ans-1::c0"
    assert claims[1].claim_id == "ans-1::c1"


def test_claim_extraction_handles_abbreviations():
    extractor = ClaimExtractor()
    text = "Dr. Smith approved the claim. The amount was 200 dollars."
    claims = extractor.extract(text, "a")
    # "Dr." must not cause a spurious split.
    assert any("Dr. Smith approved" in c.text for c in claims)
    assert len(claims) == 2


def test_claim_extraction_filters_non_factual():
    extractor = ClaimExtractor()
    text = "Thanks for reaching out. The order shipped on Monday."
    claims = extractor.extract(text, "a")
    assert len(claims) == 1
    assert "order shipped" in claims[0].text


def test_claim_extraction_empty_answer_raises():
    extractor = ClaimExtractor()
    with pytest.raises(MalformedAnswerError):
        extractor.extract("   ", "a")


# --------------------------------------------------------------------------- #
# Evidence matching
# --------------------------------------------------------------------------- #
def test_evidence_matcher_full_support():
    matcher = EvidenceMatcher()
    claims = ClaimExtractor().extract("The refund was approved on May 6.", "a")
    evidence = [Evidence("e1", "The refund was approved on May 6.", 0.9)]
    matches, best = matcher.match(claims, evidence)
    cid = claims[0].claim_id
    assert best[cid].support_level is SupportLevel.FULL_SUPPORT


def test_evidence_matcher_no_support():
    matcher = EvidenceMatcher()
    claims = ClaimExtractor().extract("The spacecraft landed on Mars.", "a")
    evidence = [Evidence("e1", "The customer requested a refund.", 0.5)]
    matches, best = matcher.match(claims, evidence)
    cid = claims[0].claim_id
    assert best[cid].support_level is SupportLevel.NO_SUPPORT


def test_evidence_matcher_empty_evidence_raises():
    from backend.grounding.errors import EmptyEvidenceError

    matcher = EvidenceMatcher()
    claims = ClaimExtractor().extract("Anything at all here.", "a")
    with pytest.raises(EmptyEvidenceError):
        matcher.match(claims, [])


# --------------------------------------------------------------------------- #
# Citation validation
# --------------------------------------------------------------------------- #
def test_citation_coverage_full():
    extractor = ClaimExtractor()
    matcher = EvidenceMatcher()
    validator = CitationValidator()
    claims = extractor.extract(
        "Order shipped on Monday. Delivery occurred on Wednesday.", "a"
    )
    evidence = [
        Evidence("e1", "Order shipped on Monday.", 0.9),
        Evidence("e2", "Delivery occurred on Wednesday.", 0.9),
    ]
    _, best = matcher.match(claims, evidence)
    report = validator.validate(claims, best, {"e1", "e2"})
    assert report.citation_coverage == 1.0
    assert not report.unsupported_claim_ids


def test_citation_dangling_detected():
    extractor = ClaimExtractor()
    matcher = EvidenceMatcher()
    validator = CitationValidator()
    claims = extractor.extract(
        "Order shipped on Monday.",
        "a",
        citations=[Citation("c1", "missing-ev", answer_span=(0, 30))],
    )
    evidence = [Evidence("e1", "Order shipped on Monday.", 0.9)]
    _, best = matcher.match(claims, evidence)
    report = validator.validate(claims, best, {"e1"})
    kinds = {f.kind for f in report.findings}
    assert "DANGLING_CITATION" in kinds


def test_citation_coverage_no_claims_is_one_with_warning():
    validator = CitationValidator()
    report = validator.validate([], {}, set())
    assert report.citation_coverage == 1.0
    assert report.warnings


# --------------------------------------------------------------------------- #
# Hallucination detection
# --------------------------------------------------------------------------- #
def test_hallucination_high_when_unsupported_exceeds_threshold():
    detector = HallucinationDetector()
    extractor = ClaimExtractor()
    matcher = EvidenceMatcher()
    claims = extractor.extract(
        "Mars has two moons. Jupiter is made of cheese. The sky is plaid.", "a"
    )
    evidence = [Evidence("e1", "Completely unrelated text about refunds.", 0.2)]
    _, best = matcher.match(claims, evidence)
    report = detector.detect(claims, best, citation_coverage=0.0)
    assert report.risk is HallucinationRisk.HIGH


def test_hallucination_low_when_all_supported():
    detector = HallucinationDetector()
    extractor = ClaimExtractor()
    matcher = EvidenceMatcher()
    claims = extractor.extract("The refund was approved on May 6.", "a")
    evidence = [Evidence("e1", "The refund was approved on May 6.", 0.9)]
    _, best = matcher.match(claims, evidence)
    report = detector.detect(claims, best, citation_coverage=1.0)
    assert report.risk is HallucinationRisk.LOW


# --------------------------------------------------------------------------- #
# Confidence scoring
# --------------------------------------------------------------------------- #
def test_confidence_high_for_strong_signals():
    scorer = ConfidenceScorer()
    b = scorer.score(
        retrieval_relevance=0.9,
        citation_coverage=1.0,
        avg_support_weight=1.0,
        hallucination_risk=HallucinationRisk.LOW,
    )
    assert b.score > 0.8


def test_confidence_penalised_by_high_risk():
    scorer = ConfidenceScorer()
    b = scorer.score(
        retrieval_relevance=0.9,
        citation_coverage=1.0,
        avg_support_weight=1.0,
        hallucination_risk=HallucinationRisk.HIGH,
    )
    # HIGH risk multiplies by 0.3.
    assert b.score < 0.4


def test_confidence_weights_must_sum_to_one():
    with pytest.raises(ConfidenceComputationError):
        ConfidenceScorer(
            ConfidenceWeights(
                retrieval_relevance=0.5,
                citation_coverage=0.5,
                evidence_support=0.5,
            )
        )


def test_confidence_clamps_out_of_range_inputs():
    scorer = ConfidenceScorer()
    b = scorer.score(
        retrieval_relevance=5.0,  # out of range
        citation_coverage=-1.0,   # out of range
        avg_support_weight=0.5,
        hallucination_risk=HallucinationRisk.LOW,
    )
    assert 0.0 <= b.score <= 1.0


# --------------------------------------------------------------------------- #
# Answer verification status transitions
# --------------------------------------------------------------------------- #
def test_verifier_verified():
    v = AnswerVerifier()
    d = v.verify(
        unsupported_count=0,
        hallucination_risk=HallucinationRisk.LOW,
        confidence_score=0.9,
    )
    assert d.status is VerificationStatus.VERIFIED


def test_verifier_partial_when_some_unsupported():
    v = AnswerVerifier()
    d = v.verify(
        unsupported_count=1,
        hallucination_risk=HallucinationRisk.MEDIUM,
        confidence_score=0.7,
    )
    assert d.status is VerificationStatus.PARTIALLY_VERIFIED


def test_verifier_rejected_on_high_risk():
    v = AnswerVerifier()
    d = v.verify(
        unsupported_count=3,
        hallucination_risk=HallucinationRisk.HIGH,
        confidence_score=0.2,
    )
    assert d.status is VerificationStatus.REJECTED


def test_verifier_downgrades_low_confidence_to_partial():
    v = AnswerVerifier()
    d = v.verify(
        unsupported_count=0,
        hallucination_risk=HallucinationRisk.LOW,
        confidence_score=0.4,  # below floor
    )
    assert d.status is VerificationStatus.PARTIALLY_VERIFIED


# --------------------------------------------------------------------------- #
# End-to-end pipeline
# --------------------------------------------------------------------------- #
def test_pipeline_fully_grounded_answer():
    pipeline, sink, metrics = make_pipeline()
    req = GroundingRequest(
        request_id="r1",
        answer_id="a1",
        answer_text=(
            "The customer requested a refund on May 4. "
            "The refund was approved on May 6."
        ),
        evidence=[
            Evidence("e1", "Customer requested a refund on May 4.", 0.95),
            Evidence("e2", "Refund approved on May 6.", 0.95),
        ],
        citations=[Citation("e1", "e1"), Citation("e2", "e2")],
    )
    result = pipeline.run(req)
    assert result.verification_status is VerificationStatus.VERIFIED
    assert result.citation_coverage == 1.0
    assert result.hallucination_risk is HallucinationRisk.LOW
    assert not result.unsupported_claims
    assert result.confidence_score > 0.7
    # Audit + metrics recorded.
    assert len(sink.records) == 1
    assert metrics.snapshot()["verified_answers"] == 1


def test_pipeline_partially_grounded_answer():
    pipeline, sink, _ = make_pipeline()
    req = GroundingRequest(
        request_id="r2",
        answer_id="a2",
        answer_text=(
            "The refund was approved on May 6. "
            "The customer also won a lottery prize of one million dollars."
        ),
        evidence=[Evidence("e1", "The refund was approved on May 6.", 0.9)],
        citations=[Citation("e1", "e1")],
    )
    result = pipeline.run(req)
    assert result.verification_status in (
        VerificationStatus.PARTIALLY_VERIFIED,
        VerificationStatus.REJECTED,
    )
    assert result.unsupported_claims  # the lottery claim is unsupported


def test_pipeline_hallucinated_answer_rejected():
    pipeline, _, metrics = make_pipeline()
    req = GroundingRequest(
        request_id="r3",
        answer_id="a3",
        answer_text=(
            "Aliens built the pyramids. The moon is hollow. Cats can fly."
        ),
        evidence=[Evidence("e1", "The invoice total was 42 dollars.", 0.3)],
    )
    result = pipeline.run(req)
    assert result.verification_status is VerificationStatus.REJECTED
    assert result.hallucination_risk is HallucinationRisk.HIGH
    assert metrics.snapshot()["rejected_answers"] == 1


def test_pipeline_empty_evidence_degrades_gracefully():
    pipeline, sink, _ = make_pipeline()
    req = GroundingRequest(
        request_id="r4",
        answer_id="a4",
        answer_text="The refund was approved on May 6.",
        evidence=[],
    )
    result = pipeline.run(req)
    assert result.verification_status is VerificationStatus.REJECTED
    assert any("evidence" in w.lower() for w in result.warnings)
    # Even a degraded result should be audited.
    assert len(sink.records) == 1


def test_pipeline_malformed_answer_degrades_gracefully():
    pipeline, _, _ = make_pipeline()
    req = GroundingRequest(
        request_id="r5",
        answer_id="a5",
        answer_text="",
        evidence=[Evidence("e1", "Something.", 0.5)],
    )
    result = pipeline.run(req)
    assert result.verification_status is VerificationStatus.REJECTED
    assert any("malformed" in w.lower() for w in result.warnings)


def test_pipeline_audit_record_contents():
    pipeline, sink, _ = make_pipeline()
    req = GroundingRequest(
        request_id="r6",
        answer_id="a6",
        answer_text="The refund was approved on May 6.",
        evidence=[Evidence("e1", "The refund was approved on May 6.", 0.9)],
        citations=[Citation("c1", "e1")],
    )
    pipeline.run(req)
    record = sink.records[0]
    assert record.request_id == "r6"
    assert record.answer_id == "a6"
    assert "e1" in record.retrieval_ids
    assert "c1" in record.citation_ids
    assert record.grounding_timestamp  # ISO timestamp present
    assert "verification_status" in record.verification_summary


def test_pipeline_audit_failure_does_not_lose_answer():
    class FailingSink(InMemoryAuditSink):
        def write(self, record):  # noqa: D401
            raise IOError("disk full")

    pipeline = GroundingPipeline(audit_trail=AuditTrail(sink=FailingSink()))
    req = GroundingRequest(
        request_id="r7",
        answer_id="a7",
        answer_text="The refund was approved on May 6.",
        evidence=[Evidence("e1", "The refund was approved on May 6.", 0.9)],
    )
    result = pipeline.run(req)
    # Answer still returned; failure surfaced as a warning.
    assert result.answer_id == "a7"
    assert any("audit write failed" in w.lower() for w in result.warnings)


def test_pipeline_timeout_degrades_gracefully():
    # Zero timeout forces the deadline check to trip.
    pipeline, _, _ = make_pipeline(timeout_seconds=0.0)
    req = GroundingRequest(
        request_id="r8",
        answer_id="a8",
        answer_text="The refund was approved on May 6.",
        evidence=[Evidence("e1", "The refund was approved on May 6.", 0.9)],
    )
    result = pipeline.run(req)
    assert result.verification_status is VerificationStatus.REJECTED
    assert any("timed out" in w.lower() for w in result.warnings)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_metrics_accumulate_across_requests():
    pipeline, _, metrics = make_pipeline()
    grounded = GroundingRequest(
        request_id="m1",
        answer_id="m1",
        answer_text="The refund was approved on May 6.",
        evidence=[Evidence("e1", "The refund was approved on May 6.", 0.9)],
    )
    hallucinated = GroundingRequest(
        request_id="m2",
        answer_id="m2",
        answer_text="Cats can fly. The moon is hollow.",
        evidence=[Evidence("e1", "Invoice total was 42 dollars.", 0.2)],
    )
    pipeline.run(grounded)
    pipeline.run(hallucinated)
    snap = metrics.snapshot()
    assert snap["grounding_requests"] == 2
    assert snap["verified_answers"] >= 1
    assert snap["rejected_answers"] >= 1
    assert 0.0 <= snap["hallucination_rate"] <= 1.0
    assert snap["verification_latency"] >= 0.0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
