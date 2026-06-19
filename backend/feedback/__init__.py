"""
backend/feedback
=================

Slice S2.4 - Feedback, Evaluation & Continuous Learning for PMOS.

This package is the feedback, evaluation, and continuous-improvement layer. It
lets PMOS capture user feedback, measure answer / citation / retrieval quality,
run structured per-stage evaluations, route flagged answers through human
review, record auditable learning signals, generate improvement
recommendations, and surface analytics - all tenant-scoped.

It explicitly does **not** retrain models. It collects and organises signals
that downstream, offline processes can use to improve prompting, ranking,
retrieval, grounding, and model selection.

Public entry point
-------------------
Most callers only need :class:`FeedbackService`, the composition root that wires
the collaborators together. The individual collaborators are also exported for
advanced wiring and testing.

Integrations
------------
* ``backend/identity``      - every artifact is scoped to a tenant/workspace/user.
* ``backend/orchestration`` - feedback is exposed as first-class workflow events.
* ``backend/grounding``     - grounding results are evaluated; hallucinations are
                              auto-flagged for review.
* ``backend/retrieval``     - retrieval outputs feed retrieval-quality scoring.
"""

from __future__ import annotations

from .analytics_event import AnalyticsEvent, AnalyticsEventType
from .analytics_service import AnalyticsService, ScopeAnalytics
from .answer_quality import AnswerQualityInput, AnswerQualityScorer
from .citation_quality import (
    CitationDescriptor,
    CitationQualityInput,
    CitationQualityScorer,
)
from .errors import (
    AnalyticsAggregationError,
    DuplicateFeedbackError,
    EvaluationError,
    FeedbackError,
    IdentityContextError,
    InvalidRatingError,
    InvalidReviewTransitionError,
    MissingAnswerError,
    MissingCitationError,
    MissingFeedbackError,
    QualityScoringError,
    ReviewItemNotFoundError,
    ReviewWorkflowError,
)
from .evaluation_engine import EvaluationContext, EvaluationEngine
from .evaluation_result import EvaluationResult, EvaluationStage, StageEvaluation
from .feedback_aggregator import FeedbackAggregator, FeedbackSummary
from .feedback_collector import (
    FeedbackCollector,
    FeedbackStore,
    FeedbackSubmission,
    InMemoryFeedbackStore,
)
from .feedback_event import (
    EventPublisher,
    FeedbackEvent,
    FeedbackEventKind,
    InMemoryEventPublisher,
)
from .feedback_record import FeedbackRecord, FeedbackType
from .feedback_service import EvaluationReport, FeedbackService
from .human_review import HumanReviewService
from .identity import IdentityContext, IdentityResolver
from .improvement_recommendation import (
    ImprovementRecommendation,
    RecommendationCategory,
    RecommendationEngine,
    Severity,
)
from .learning_event import LearningEvent, LearningEventLog, LearningEventType
from .metrics import (
    FeedbackMetrics,
    InMemoryMetricsSink,
    MetricNames,
    MetricsSink,
    NullMetricsSink,
)
from .quality_score import QualityScore, QualityScoreSet, clamp_unit
from .quality_scorer import (
    QualityWeights,
    UnifiedQualityInput,
    UnifiedQualityResult,
    UnifiedQualityScorer,
)
from .rating import ConfidenceFeedback, StarRating, UsefulnessFeedback
from .reaction import Reaction
from .retrieval_quality import (
    RetrievalQualityInput,
    RetrievalQualityScorer,
    RetrievedChunk,
)
from .review_item import ReviewItem, ReviewOutcome, ReviewReason, ReviewStatus
from .review_queue import ReviewQueue

__version__ = "2.4.0"

__all__ = [
    "__version__",
    # service / facade
    "FeedbackService",
    "EvaluationReport",
    # capture
    "FeedbackCollector",
    "FeedbackSubmission",
    "FeedbackStore",
    "InMemoryFeedbackStore",
    "FeedbackRecord",
    "FeedbackType",
    "Reaction",
    "StarRating",
    "ConfidenceFeedback",
    "UsefulnessFeedback",
    # events
    "FeedbackEvent",
    "FeedbackEventKind",
    "EventPublisher",
    "InMemoryEventPublisher",
    # quality
    "QualityScore",
    "QualityScoreSet",
    "clamp_unit",
    "AnswerQualityInput",
    "AnswerQualityScorer",
    "CitationDescriptor",
    "CitationQualityInput",
    "CitationQualityScorer",
    "RetrievedChunk",
    "RetrievalQualityInput",
    "RetrievalQualityScorer",
    "UnifiedQualityScorer",
    "UnifiedQualityInput",
    "UnifiedQualityResult",
    "QualityWeights",
    # evaluation
    "EvaluationEngine",
    "EvaluationContext",
    "EvaluationResult",
    "EvaluationStage",
    "StageEvaluation",
    # review
    "HumanReviewService",
    "ReviewQueue",
    "ReviewItem",
    "ReviewReason",
    "ReviewStatus",
    "ReviewOutcome",
    # learning & recommendations
    "LearningEvent",
    "LearningEventLog",
    "LearningEventType",
    "ImprovementRecommendation",
    "RecommendationEngine",
    "RecommendationCategory",
    "Severity",
    # aggregation & analytics
    "FeedbackAggregator",
    "FeedbackSummary",
    "AnalyticsService",
    "ScopeAnalytics",
    "AnalyticsEvent",
    "AnalyticsEventType",
    # identity
    "IdentityContext",
    "IdentityResolver",
    # metrics
    "FeedbackMetrics",
    "MetricsSink",
    "MetricNames",
    "InMemoryMetricsSink",
    "NullMetricsSink",
    # errors
    "FeedbackError",
    "MissingFeedbackError",
    "InvalidRatingError",
    "DuplicateFeedbackError",
    "MissingAnswerError",
    "MissingCitationError",
    "EvaluationError",
    "QualityScoringError",
    "ReviewWorkflowError",
    "ReviewItemNotFoundError",
    "InvalidReviewTransitionError",
    "AnalyticsAggregationError",
    "IdentityContextError",
]
