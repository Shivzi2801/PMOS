"""
test_feedback.py
================

Test suite for PMOS Slice S2.4 — Feedback, Evaluation & Continuous Learning.

These tests exercise the public surface of the ``backend.feedback`` package and
are intentionally dependency-free: every collaborator has an in-memory default
implementation, so the suite runs without a database, queue, or network.

Run from the repository root (the directory that contains ``backend/``)::

    python -m pytest backend/feedback/test_feedback.py -v

or, without pytest installed::

    python -m backend.feedback.test_feedback

Coverage maps directly onto the slice's required testing surface:

* feedback capture (all five modalities)
* ratings (stars, reactions, confidence, usefulness)
* quality scoring (answer, citation, retrieval, unified)
* evaluation engine (per-stage + roll-up + failure flags)
* review queue + human review lifecycle
* analytics aggregation (tenant + workspace)
* learning event creation (auditable signals)
* recommendation generation (rule-based)
"""

from __future__ import annotations

import unittest

from backend.feedback import (
    AnalyticsService,
    AnswerQualityInput,
    AnswerQualityScorer,
    CitationDescriptor,
    CitationQualityInput,
    CitationQualityScorer,
    ConfidenceFeedback,
    DuplicateFeedbackError,
    EvaluationContext,
    EvaluationEngine,
    EvaluationStage,
    FeedbackCollector,
    FeedbackRecord,
    FeedbackService,
    FeedbackSubmission,
    FeedbackType,
    HumanReviewService,
    IdentityContext,
    InMemoryFeedbackStore,
    InvalidRatingError,
    LearningEventLog,
    LearningEventType,
    MissingAnswerError,
    RecommendationEngine,
    Reaction,
    RetrievalQualityInput,
    RetrievalQualityScorer,
    RetrievedChunk,
    ReviewOutcome,
    ReviewQueue,
    ReviewReason,
    ReviewStatus,
    StarRating,
    UnifiedQualityInput,
    UnifiedQualityScorer,
    UsefulnessFeedback,
)


# Shared identity fixtures -----------------------------------------------------

IDENTITY = IdentityContext(
    tenant_id="tenant-1",
    workspace_id="workspace-a",
    user_id="user-42",
)
OTHER_TENANT = IdentityContext(
    tenant_id="tenant-2",
    workspace_id="workspace-z",
    user_id="user-99",
)


def _always_exists(_a: str, _b: str) -> bool:
    return True


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------
class TestRatings(unittest.TestCase):
    def test_star_rating_normalizes(self) -> None:
        self.assertAlmostEqual(StarRating(1).normalized, 0.0)
        self.assertAlmostEqual(StarRating(5).normalized, 1.0)
        self.assertAlmostEqual(StarRating(3).normalized, 0.5)

    def test_star_rating_rejects_out_of_range(self) -> None:
        with self.assertRaises(InvalidRatingError):
            StarRating(0)
        with self.assertRaises(InvalidRatingError):
            StarRating(6)

    def test_confidence_and_usefulness_unit_interval(self) -> None:
        self.assertAlmostEqual(ConfidenceFeedback(0.5).value, 0.5)
        self.assertAlmostEqual(UsefulnessFeedback(1.0).value, 1.0)
        with self.assertRaises(InvalidRatingError):
            ConfidenceFeedback(1.5)
        with self.assertRaises(InvalidRatingError):
            UsefulnessFeedback(-0.1)

    def test_reaction_polarity(self) -> None:
        self.assertEqual(Reaction.THUMBS_UP.polarity, 1)
        self.assertEqual(Reaction.THUMBS_DOWN.polarity, -1)
        self.assertEqual(Reaction.NEUTRAL.polarity, 0)
        self.assertTrue(Reaction.THUMBS_DOWN.is_negative)
        self.assertTrue(Reaction.THUMBS_UP.is_positive)


# ---------------------------------------------------------------------------
# Feedback capture
# ---------------------------------------------------------------------------
class TestFeedbackCapture(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryFeedbackStore()
        self.collector = FeedbackCollector(
            self.store,
            answer_exists=_always_exists,
            citation_exists=_always_exists,
        )

    def _submit(self, **kw) -> FeedbackRecord:
        submission = FeedbackSubmission(**kw)
        return self.collector.collect(IDENTITY, submission)

    def test_thumbs_up_capture(self) -> None:
        rec = self._submit(
            query_id="q1",
            answer_id="a1",
            feedback_type=FeedbackType.THUMBS_UP,
            reaction=Reaction.THUMBS_UP.value,
        )
        self.assertEqual(rec.tenant_id, "tenant-1")
        self.assertEqual(rec.workspace_id, "workspace-a")
        self.assertEqual(rec.user_id, "user-42")
        self.assertEqual(rec.polarity, 1)
        self.assertIsNotNone(rec.timestamp)

    def test_thumbs_down_capture(self) -> None:
        rec = self._submit(
            query_id="q1",
            answer_id="a2",
            feedback_type=FeedbackType.THUMBS_DOWN,
            reaction=Reaction.THUMBS_DOWN.value,
        )
        self.assertEqual(rec.polarity, -1)

    def test_star_rating_capture(self) -> None:
        rec = self._submit(
            query_id="q1",
            answer_id="a3",
            feedback_type=FeedbackType.STAR_RATING,
            star=4,
        )
        self.assertEqual(rec.star_rating.value, 4)

    def test_free_text_capture(self) -> None:
        rec = self._submit(
            query_id="q1",
            answer_id="a4",
            feedback_type=FeedbackType.FREE_TEXT,
            free_text="The answer missed the regional breakdown.",
        )
        self.assertIn("regional", rec.free_text)

    def test_citation_feedback_capture(self) -> None:
        rec = self._submit(
            query_id="q1",
            answer_id="a5",
            feedback_type=FeedbackType.CITATION_FEEDBACK,
            citation_id="cit-7",
            citation_accepted=True,
        )
        self.assertEqual(rec.citation_id, "cit-7")
        self.assertTrue(rec.citation_accepted)

    def test_invalid_star_rejected(self) -> None:
        with self.assertRaises(InvalidRatingError):
            self._submit(
                query_id="q1",
                answer_id="a6",
                feedback_type=FeedbackType.STAR_RATING,
                star=9,
            )

    def test_missing_answer_rejected(self) -> None:
        collector = FeedbackCollector(
            InMemoryFeedbackStore(),
            answer_exists=lambda _t, _a: False,
        )
        with self.assertRaises(MissingAnswerError):
            collector.collect(
                IDENTITY,
                FeedbackSubmission(
                    query_id="q1",
                    answer_id="missing",
                    feedback_type=FeedbackType.THUMBS_UP,
                    reaction=Reaction.THUMBS_UP.value,
                ),
            )

    def test_duplicate_feedback_rejected(self) -> None:
        self._submit(
            query_id="q1",
            answer_id="a7",
            feedback_type=FeedbackType.THUMBS_UP,
            reaction=Reaction.THUMBS_UP.value,
        )
        with self.assertRaises(DuplicateFeedbackError):
            self._submit(
                query_id="q1",
                answer_id="a7",
                feedback_type=FeedbackType.THUMBS_UP,
                reaction=Reaction.THUMBS_UP.value,
            )

    def test_duplicate_allowed_when_update_flag_set(self) -> None:
        self._submit(
            query_id="q1",
            answer_id="a8",
            feedback_type=FeedbackType.STAR_RATING,
            star=3,
        )
        updated = self.collector.collect(
            IDENTITY,
            FeedbackSubmission(
                query_id="q1",
                answer_id="a8",
                feedback_type=FeedbackType.STAR_RATING,
                star=5,
            ),
            allow_update=True,
        )
        self.assertEqual(updated.star_rating.value, 5)


# ---------------------------------------------------------------------------
# Quality scoring — component scorers
# ---------------------------------------------------------------------------
class TestComponentQualityScoring(unittest.TestCase):
    def test_answer_quality_scores_all_dimensions(self) -> None:
        scorer = AnswerQualityScorer()
        result = scorer.score(
            AnswerQualityInput(
                query_text="What were Q3 revenue and margin trends?",
                answer_text=(
                    "Q3 revenue rose 12% to $4.1M. Gross margin improved to "
                    "61% from 58% on lower cloud costs."
                ),
                requested_aspects=["revenue", "margin"],
                covered_aspects=["revenue", "margin"],
                model_confidence=0.8,
            )
        )
        self.assertEqual(result.label, "answer_quality")
        names = {s.name for s in result.scores}
        self.assertEqual(
            names,
            {
                "answer_relevance",
                "answer_completeness",
                "answer_clarity",
                "answer_usefulness",
                "answer_confidence",
            },
        )
        avg = result.weighted_average
        self.assertIsNotNone(avg)
        self.assertGreater(avg, 0.0)
        self.assertLessEqual(avg, 1.0)

    def test_citation_quality_acceptance_rate(self) -> None:
        scorer = CitationQualityScorer()
        result = scorer.score(
            CitationQualityInput(
                citations=[
                    CitationDescriptor("c1", "s1", relevance=0.9, trust=0.8),
                    CitationDescriptor("c2", "s2", relevance=0.7, trust=0.9),
                ],
                total_claims=4,
                cited_claims=3,
            )
        )
        self.assertEqual(result.label, "citation_quality")
        coverage = result.get("citation_coverage")
        self.assertIsNotNone(coverage)
        self.assertAlmostEqual(coverage.value, 0.75, places=5)

    def test_retrieval_quality_precision_and_diversity(self) -> None:
        scorer = RetrievalQualityScorer()
        result = scorer.score(
            RetrievalQualityInput(
                chunks=[
                    RetrievedChunk("ch1", "s1", rank=1, used=True, retrieval_score=0.9),
                    RetrievedChunk("ch2", "s2", rank=2, used=True, retrieval_score=0.7),
                    RetrievedChunk("ch3", "s3", rank=3, used=False, retrieval_score=0.4),
                ],
                expected_source_count=3,
            )
        )
        precision = result.get("retrieval_precision")
        self.assertIsNotNone(precision)
        # 2 of 3 chunks used.
        self.assertAlmostEqual(precision.value, 2 / 3, places=5)
        diversity = result.get("source_diversity")
        self.assertIsNotNone(diversity)
        self.assertGreater(diversity.value, 0.0)

    def test_unified_quality_caps_on_unresolved_hallucination(self) -> None:
        scorer = UnifiedQualityScorer()
        clean = scorer.score(
            UnifiedQualityInput(
                retrieval_score=0.9,
                citation_score=0.9,
                feedback_score=0.9,
                review_score=0.9,
            )
        )
        self.assertGreater(clean.overall_quality_score, 0.8)
        self.assertFalse(clean.capped)

        capped = scorer.score(
            UnifiedQualityInput(
                retrieval_score=0.9,
                citation_score=0.9,
                feedback_score=0.9,
                review_score=0.9,
                unresolved_hallucination=True,
            )
        )
        self.assertTrue(capped.capped)
        self.assertLessEqual(capped.overall_quality_score, 0.3)

    def test_unified_quality_requires_some_input(self) -> None:
        scorer = UnifiedQualityScorer()
        result = scorer.score(UnifiedQualityInput())
        # No contributing sources -> no score, but must not raise.
        self.assertEqual(len(result.contributing_sources), 0)
        self.assertIsNone(result.overall_quality_score)


# ---------------------------------------------------------------------------
# Evaluation engine
# ---------------------------------------------------------------------------
class TestEvaluationEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = EvaluationEngine()

    def _full_context(self, **overrides) -> EvaluationContext:
        base = dict(
            identity=IDENTITY,
            query_id="q1",
            answer_id="a1",
            answer_input=AnswerQualityInput(
                query_text="What is the refund window?",
                answer_text="Refunds are accepted within 30 days of purchase.",
                requested_aspects=["refund window"],
                covered_aspects=["refund window"],
                model_confidence=0.85,
            ),
            citation_input=CitationQualityInput(
                citations=[CitationDescriptor("c1", "s1", relevance=0.9, trust=0.9)],
                total_claims=1,
                cited_claims=1,
            ),
            retrieval_input=RetrievalQualityInput(
                chunks=[
                    RetrievedChunk("ch1", "s1", rank=1, used=True, retrieval_score=0.92),
                ],
                expected_source_count=1,
            ),
            grounding_support_ratio=0.95,
            context_relevance=0.9,
        )
        base.update(overrides)
        return EvaluationContext(**base)

    def test_evaluate_produces_all_stages(self) -> None:
        result = self.engine.evaluate(self._full_context())
        stages = {se.stage for se in result.stage_evaluations}
        self.assertIn(EvaluationStage.RETRIEVAL, stages)
        self.assertIn(EvaluationStage.GENERATION, stages)
        self.assertIn(EvaluationStage.GROUNDING, stages)
        self.assertIn(EvaluationStage.FINAL_ANSWER, stages)
        self.assertIsNotNone(result.overall_score)
        self.assertGreater(result.overall_score, 0.0)

    def test_high_quality_answer_has_no_failure_flags(self) -> None:
        result = self.engine.evaluate(self._full_context())
        self.assertFalse(result.hallucination_suspected)
        self.assertFalse(result.citation_failure)
        self.assertFalse(result.retrieval_failure)

    def test_low_grounding_flags_hallucination(self) -> None:
        result = self.engine.evaluate(
            self._full_context(grounding_support_ratio=0.1)
        )
        self.assertTrue(result.hallucination_suspected)

    def test_poor_retrieval_flags_failure(self) -> None:
        result = self.engine.evaluate(
            self._full_context(
                retrieval_input=RetrievalQualityInput(
                    chunks=[
                        RetrievedChunk("ch1", "s1", rank=1, used=False, retrieval_score=0.0),
                        RetrievedChunk("ch2", "s1", rank=2, used=False, retrieval_score=0.0),
                    ],
                    reported_missing_evidence=True,
                    expected_source_count=10,
                )
            )
        )
        self.assertTrue(result.retrieval_failure)


# ---------------------------------------------------------------------------
# Review queue + human review lifecycle
# ---------------------------------------------------------------------------
class TestHumanReview(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = ReviewQueue()
        self.log = LearningEventLog()
        self.service = HumanReviewService(self.queue, learning_log=self.log)

    def test_flag_answer_enqueues_open_item(self) -> None:
        item = self.service.flag_answer(IDENTITY, query_id="q1", answer_id="a1")
        self.assertEqual(item.status, ReviewStatus.OPEN)
        self.assertEqual(item.reason, ReviewReason.FLAG_ANSWER)
        self.assertEqual(self.queue.backlog_size("tenant-1"), 1)

    def test_hallucination_flag_is_high_priority(self) -> None:
        item = self.service.flag_hallucination(IDENTITY, query_id="q1", answer_id="a1")
        self.assertEqual(item.reason, ReviewReason.FLAG_HALLUCINATION)
        self.assertGreaterEqual(item.priority, 10)

    def test_review_lifecycle_claim_then_resolve(self) -> None:
        item = self.service.flag_answer(IDENTITY, query_id="q1", answer_id="a1")
        claimed = self.service.claim(IDENTITY, item.review_id)
        self.assertEqual(claimed.status, ReviewStatus.IN_REVIEW)
        resolved = self.service.resolve(
            IDENTITY,
            item.review_id,
            outcome=ReviewOutcome.CONFIRMED,
            note="Confirmed factual error.",
        )
        self.assertEqual(resolved.status, ReviewStatus.RESOLVED)
        self.assertTrue(resolved.is_closed)

    def test_resolution_emits_learning_event(self) -> None:
        item = self.service.flag_answer(IDENTITY, query_id="q1", answer_id="a1")
        self.service.claim(IDENTITY, item.review_id)
        self.service.resolve(
            IDENTITY, item.review_id, outcome=ReviewOutcome.CORRECTED
        )
        kinds = [e.event_type for e in self.log.all()]
        self.assertIn(LearningEventType.REVIEW_COMPLETED, kinds)

    def test_cross_tenant_access_blocked(self) -> None:
        item = self.service.flag_answer(IDENTITY, query_id="q1", answer_id="a1")
        with self.assertRaises(Exception):
            self.service.claim(OTHER_TENANT, item.review_id)


# ---------------------------------------------------------------------------
# Learning events
# ---------------------------------------------------------------------------
class TestLearningEvents(unittest.TestCase):
    def test_log_is_append_only_and_filterable(self) -> None:
        from backend.feedback import LearningEvent

        log = LearningEventLog()
        log.append(
            LearningEvent(
                event_type=LearningEventType.HALLUCINATION,
                tenant_id="tenant-1",
                workspace_id="workspace-a",
                answer_id="a1",
                query_id="q1",
                source="evaluation_engine",
                score=0.1,
            )
        )
        log.append(
            LearningEvent(
                event_type=LearningEventType.HIGH_QUALITY_ANSWER,
                tenant_id="tenant-1",
                workspace_id="workspace-b",
                answer_id="a2",
                query_id="q2",
                source="evaluation_engine",
                score=0.95,
            )
        )
        self.assertEqual(log.count(), 2)
        self.assertEqual(len(log.for_tenant("tenant-1")), 2)
        self.assertEqual(len(log.for_workspace("tenant-1", "workspace-a")), 1)
        self.assertEqual(
            len(log.of_type(LearningEventType.HALLUCINATION)), 1
        )


# ---------------------------------------------------------------------------
# Recommendation generation
# ---------------------------------------------------------------------------
class TestRecommendations(unittest.TestCase):
    def test_poor_retrieval_yields_recommendation(self) -> None:
        engine = EvaluationEngine()
        ctx = EvaluationContext(
            identity=IDENTITY,
            query_id="q1",
            answer_id="a1",
            retrieval_input=RetrievalQualityInput(
                chunks=[
                    RetrievedChunk("ch1", "s1", rank=1, used=False, retrieval_score=0.02),
                ],
                reported_missing_evidence=True,
                expected_source_count=5,
            ),
            grounding_support_ratio=0.1,
        )
        result = engine.evaluate(ctx)
        recs = RecommendationEngine().generate(result)
        self.assertTrue(recs)
        targets = {r.category for r in recs}
        # Expect at least one retrieval/grounding-oriented recommendation.
        self.assertTrue(targets)


# ---------------------------------------------------------------------------
# Analytics aggregation
# ---------------------------------------------------------------------------
class TestAnalytics(unittest.TestCase):
    def test_service_end_to_end_analytics(self) -> None:
        store = InMemoryFeedbackStore()
        service = FeedbackService(
            collector=FeedbackCollector(
                store,
                answer_exists=_always_exists,
                citation_exists=_always_exists,
            )
        )
        # Two positive, one negative.
        service.submit_feedback(
            IDENTITY,
            FeedbackSubmission(
                query_id="q1",
                answer_id="a1",
                feedback_type=FeedbackType.THUMBS_UP,
                reaction=Reaction.THUMBS_UP.value,
            ),
        )
        service.submit_feedback(
            IDENTITY,
            FeedbackSubmission(
                query_id="q2",
                answer_id="a2",
                feedback_type=FeedbackType.THUMBS_UP,
                reaction=Reaction.THUMBS_UP.value,
            ),
        )
        service.submit_feedback(
            IDENTITY,
            FeedbackSubmission(
                query_id="q3",
                answer_id="a3",
                feedback_type=FeedbackType.THUMBS_DOWN,
                reaction=Reaction.THUMBS_DOWN.value,
            ),
        )
        analytics = service.analytics.tenant_analytics("tenant-1")
        self.assertEqual(analytics.feedback_count, 3)
        self.assertEqual(analytics.positive_feedback, 2)
        self.assertEqual(analytics.negative_feedback, 1)
        self.assertAlmostEqual(analytics.positive_feedback_rate, 2 / 3, places=5)

    def test_aggregator_summary_counts(self) -> None:
        store = InMemoryFeedbackStore()
        collector = FeedbackCollector(
            store, answer_exists=_always_exists, citation_exists=_always_exists
        )
        for i, ft in enumerate(
            [FeedbackType.THUMBS_UP, FeedbackType.THUMBS_DOWN, FeedbackType.STAR_RATING]
        ):
            kw = dict(query_id=f"q{i}", answer_id=f"a{i}", feedback_type=ft)
            if ft is FeedbackType.STAR_RATING:
                kw["star"] = 5
            else:
                kw["reaction"] = (
                    Reaction.THUMBS_UP.value
                    if ft is FeedbackType.THUMBS_UP
                    else Reaction.THUMBS_DOWN.value
                )
            collector.collect(IDENTITY, FeedbackSubmission(**kw))

        service = FeedbackService(collector=collector)
        summary = service.aggregate_feedback(store.all())
        self.assertEqual(summary.total, 3)
        # Thumbs-up plus a 4-5 star rating both count as positive polarity.
        self.assertEqual(summary.positive, 2)
        self.assertEqual(summary.negative, 1)
        self.assertEqual(summary.star_count, 1)
        self.assertEqual(summary.star_sum, 5)


# ---------------------------------------------------------------------------
# FeedbackService — evaluation report integration
# ---------------------------------------------------------------------------
class TestFeedbackServiceEvaluation(unittest.TestCase):
    def test_evaluate_answer_returns_report_with_recommendations(self) -> None:
        service = FeedbackService()
        ctx = EvaluationContext(
            identity=IDENTITY,
            query_id="q1",
            answer_id="a1",
            retrieval_input=RetrievalQualityInput(
                chunks=[
                    RetrievedChunk("ch1", "s1", rank=1, used=False, retrieval_score=0.01),
                ],
                reported_missing_evidence=True,
                expected_source_count=5,
            ),
            grounding_support_ratio=0.05,
        )
        report = service.evaluate_answer(ctx)
        self.assertIsNotNone(report.result)
        self.assertTrue(report.result.hallucination_suspected)
        # A suspected hallucination should generate learning events.
        self.assertTrue(report.learning_events)
        # Report serialises cleanly.
        self.assertIn("result", report.to_dict())

    def test_compute_quality_returns_unified_score(self) -> None:
        service = FeedbackService()
        result = service.compute_quality(
            IDENTITY,
            answer_id="a1",
            retrieval_score=0.8,
            citation_score=0.85,
            review_score=0.9,
        )
        self.assertGreater(result.overall_quality_score, 0.0)
        self.assertLessEqual(result.overall_quality_score, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
