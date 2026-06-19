"""
backend/feedback/feedback_service.py

The feedback service: the top-level facade for the S2.4 subsystem.

This is the single object the rest of PMOS (the API layer, the orchestration
layer) talks to. It wires the collaborators together and exposes a small,
intention-revealing surface:

* :meth:`submit_feedback`  - validate + store feedback, emit events/analytics,
                             update metrics.
* :meth:`evaluate_answer`  - run the evaluation engine, derive learning events,
                             generate improvement recommendations.
* :meth:`compute_quality`  - combine retrieval/citation/feedback/review signals
                             into the unified ``overall_quality_score``.

Integration summary (the "wiring diagram"):

    API / orchestration
            |
            v
      FeedbackService  ── identity ──> IdentityContext (backend/identity)
            |  \\
            |   \\── events ──> EventPublisher (backend/orchestration)
            |
   ┌────────┼────────────────────────────────────────────┐
   v        v               v               v             v
collector  evaluation   unified-scorer  human-review  analytics
            engine                          service     service
                |                              |
            learning-log <─────────────────────┘
                |
         recommendation-engine

The service is deliberately thin: each collaborator is independently testable,
and the service's job is composition and event fan-out, not business rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .analytics_event import AnalyticsEvent, AnalyticsEventType
from .analytics_service import AnalyticsService
from .evaluation_engine import EvaluationContext, EvaluationEngine
from .evaluation_result import EvaluationResult
from .feedback_aggregator import FeedbackAggregator, FeedbackSummary
from .feedback_collector import FeedbackCollector, FeedbackSubmission, InMemoryFeedbackStore
from .feedback_event import (
    EventPublisher,
    FeedbackEvent,
    FeedbackEventKind,
)
from .feedback_record import FeedbackRecord
from .human_review import HumanReviewService
from .identity import IdentityContext
from .improvement_recommendation import ImprovementRecommendation, RecommendationEngine
from .learning_event import LearningEvent, LearningEventLog, LearningEventType
from .metrics import FeedbackMetrics
from .quality_scorer import (
    UnifiedQualityInput,
    UnifiedQualityResult,
    UnifiedQualityScorer,
)


@dataclass
class EvaluationReport:
    """Bundle returned by :meth:`FeedbackService.evaluate_answer`."""

    result: EvaluationResult
    learning_events: List[LearningEvent]
    recommendations: List[ImprovementRecommendation]

    def to_dict(self):
        return {
            "result": self.result.to_dict(),
            "learning_events": [e.to_dict() for e in self.learning_events],
            "recommendations": [r.to_dict() for r in self.recommendations],
        }


# Quality thresholds for deriving learning events from an evaluation.
POOR_ANSWER_THRESHOLD = 0.4
HIGH_QUALITY_THRESHOLD = 0.8


class FeedbackService:
    """Composition root for the feedback / evaluation / learning subsystem."""

    def __init__(
        self,
        *,
        metrics: Optional[FeedbackMetrics] = None,
        publisher: Optional[EventPublisher] = None,
        collector: Optional[FeedbackCollector] = None,
        evaluation_engine: Optional[EvaluationEngine] = None,
        aggregator: Optional[FeedbackAggregator] = None,
        unified_scorer: Optional[UnifiedQualityScorer] = None,
        review_service: Optional[HumanReviewService] = None,
        analytics: Optional[AnalyticsService] = None,
        learning_log: Optional[LearningEventLog] = None,
        recommendation_engine: Optional[RecommendationEngine] = None,
    ) -> None:
        self.metrics = metrics or FeedbackMetrics()
        self.publisher = publisher or EventPublisher()
        self.learning_log = learning_log or LearningEventLog()
        self.collector = collector or FeedbackCollector(
            InMemoryFeedbackStore(), metrics=self.metrics
        )
        self.evaluation_engine = evaluation_engine or EvaluationEngine(self.metrics)
        self.aggregator = aggregator or FeedbackAggregator()
        self.unified_scorer = unified_scorer or UnifiedQualityScorer()
        self.review_service = review_service or HumanReviewService(
            learning_log=self.learning_log, metrics=self.metrics
        )
        self.analytics = analytics or AnalyticsService()
        self.recommendation_engine = recommendation_engine or RecommendationEngine()

    # -- feedback capture ----------------------------------------------
    def submit_feedback(
        self,
        identity: IdentityContext,
        submission: FeedbackSubmission,
        *,
        allow_update: bool = False,
    ) -> FeedbackRecord:
        """Validate, store, and fan out a single piece of feedback."""
        record = self.collector.collect(identity, submission, allow_update=allow_update)

        # Emit the orchestration event so downstream workflows can react.
        self.publisher.publish(
            FeedbackEvent(
                kind=FeedbackEventKind.FEEDBACK_SUBMITTED,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                user_id=record.user_id,
                payload={
                    "feedback_id": record.feedback_id,
                    "answer_id": record.answer_id,
                    "feedback_type": record.feedback_type.value,
                    "polarity": record.polarity,
                },
            )
        )

        # Record analytics facts.
        self.analytics.record(
            AnalyticsEvent(
                event_type=AnalyticsEventType.FEEDBACK_RECEIVED,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                attributes={"polarity": record.polarity},
            )
        )
        if record.citation_accepted is not None:
            self.analytics.record(
                AnalyticsEvent(
                    event_type=AnalyticsEventType.CITATION_DECISION,
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    attributes={"accepted": bool(record.citation_accepted)},
                )
            )

        self.metrics.feedback_processed(labels=identity.scope_labels)
        self.publisher.publish(
            FeedbackEvent(
                kind=FeedbackEventKind.FEEDBACK_PROCESSED,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                payload={"feedback_id": record.feedback_id},
            )
        )
        return record

    # -- evaluation -----------------------------------------------------
    def evaluate_answer(self, ctx: EvaluationContext) -> EvaluationReport:
        """Run an evaluation, derive learning events, and generate recommendations."""
        result = self.evaluation_engine.evaluate(ctx)

        events = self._derive_learning_events(result)
        for ev in events:
            self.learning_log.append(ev)

        recommendations = self.recommendation_engine.generate(result)
        if recommendations:
            self.metrics.recommendation_generated(
                len(recommendations), labels=ctx.identity.scope_labels
            )

        # Analytics: record the completed evaluation with its overall score.
        self.analytics.record(
            AnalyticsEvent(
                event_type=AnalyticsEventType.EVALUATION_COMPLETED,
                tenant_id=result.tenant_id,
                workspace_id=result.workspace_id,
                value=result.overall_score,
            )
        )

        # Auto-flag hallucinations for human review.
        if result.hallucination_suspected:
            self.review_service.flag_hallucination(
                ctx.identity,
                query_id=result.query_id,
                answer_id=result.answer_id,
                note="Auto-flagged by evaluation engine.",
            )

        return EvaluationReport(
            result=result, learning_events=events, recommendations=recommendations
        )

    def _derive_learning_events(self, result: EvaluationResult) -> List[LearningEvent]:
        events: List[LearningEvent] = []

        def mk(event_type: LearningEventType, **detail) -> LearningEvent:
            return LearningEvent(
                event_type=event_type,
                tenant_id=result.tenant_id,
                workspace_id=result.workspace_id,
                answer_id=result.answer_id,
                query_id=result.query_id,
                source="evaluation_engine",
                score=result.overall_score,
                detail=detail,
            )

        if result.hallucination_suspected:
            events.append(mk(LearningEventType.HALLUCINATION))
            self.metrics.hallucination_report()
        if result.citation_failure:
            events.append(mk(LearningEventType.CITATION_FAILURE))
            self.metrics.citation_failure()
        if result.retrieval_failure:
            events.append(mk(LearningEventType.RETRIEVAL_FAILURE))
        if result.overall_score is not None:
            if result.overall_score < POOR_ANSWER_THRESHOLD:
                events.append(mk(LearningEventType.POOR_ANSWER))
            elif result.overall_score >= HIGH_QUALITY_THRESHOLD:
                events.append(mk(LearningEventType.HIGH_QUALITY_ANSWER))
        return events

    # -- unified quality ------------------------------------------------
    def compute_quality(
        self,
        identity: IdentityContext,
        *,
        answer_id: str,
        retrieval_score: Optional[float] = None,
        citation_score: Optional[float] = None,
        review_score: Optional[float] = None,
        feedback_records: Optional[List[FeedbackRecord]] = None,
        unresolved_hallucination: bool = False,
    ) -> UnifiedQualityResult:
        """Combine all signal sources into the unified ``overall_quality_score``."""
        feedback_score = None
        if feedback_records:
            feedback_score = UnifiedQualityScorer.feedback_score_from_records(
                feedback_records
            )
        result = self.unified_scorer.score(
            UnifiedQualityInput(
                retrieval_score=retrieval_score,
                citation_score=citation_score,
                feedback_score=feedback_score,
                review_score=review_score,
                unresolved_hallucination=unresolved_hallucination,
            )
        )
        if result.overall_quality_score is not None:
            self.metrics.quality_score(
                result.overall_quality_score, labels=identity.scope_labels
            )
        return result

    # -- aggregation passthrough ---------------------------------------
    def aggregate_feedback(self, records) -> FeedbackSummary:
        return self.aggregator.aggregate(records)


__all__ = ["FeedbackService", "EvaluationReport"]
