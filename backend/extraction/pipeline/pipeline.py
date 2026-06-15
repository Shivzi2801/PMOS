"""ExtractionPipeline: orchestrates the cheap-first cascade.

Flow:
    CanonicalDocument
        -> validate
        -> RuleBasedExtractor (stage 1)
        -> HeuristicExtractor (stage 2, fallback when stage 1 empty)
        -> [LLMExtractor stage 3: interface only, not invoked in S1.3]
        -> ConfidenceScorer (validate/normalize per-method bands)
        -> AtomRanker (dedup + merge)
        -> ExtractionResult

Failure handling:
  * Malformed/None documents raise MalformedDocumentError, increment
    extraction_failures_total, and re-raise (terminal, non-retryable).
  * An unexpected fault inside a single extractor is isolated: the stage is
    skipped, extraction_failures_total is incremented with a stage label, and
    the cascade continues. A document never fails wholesale because one
    optional stage misbehaved.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from ..confidence.scorer import ConfidenceScorer, ExtractionMethod
from ..contracts.atoms import Atom
from ..contracts.canonical_document import (
    CanonicalDocument,
    validate_canonical_document,
)
from ..contracts.errors import MalformedDocumentError
from ..contracts.results import EXTRACTION_VERSION, ExtractionContext, ExtractionResult
from ..extractors.base import Extractor
from ..extractors.heuristic import HeuristicExtractor
from ..extractors.llm import LLMExtractor, NullLLMExtractor
from ..extractors.rule_based import RuleBasedExtractor
from ..ranking.ranker import AtomRanker
from .metrics import (
    M_ATOMS_TOTAL,
    M_DOCUMENTS_TOTAL,
    M_FAILURES_TOTAL,
    M_LATENCY_MS,
    MetricsSink,
    NullMetricsSink,
)

logger = logging.getLogger("extraction.pipeline")


class ExtractionPipeline:
    """Production extraction pipeline for Slice 1.3 (FactAtom only)."""

    def __init__(
        self,
        rule_extractor: Optional[RuleBasedExtractor] = None,
        heuristic_extractor: Optional[HeuristicExtractor] = None,
        llm_extractor: Optional[LLMExtractor] = None,
        scorer: Optional[ConfidenceScorer] = None,
        ranker: Optional[AtomRanker] = None,
        metrics: Optional[MetricsSink] = None,
    ) -> None:
        self._rule = rule_extractor or RuleBasedExtractor()
        self._heuristic = heuristic_extractor or HeuristicExtractor()
        # Stage 3 is interface-only in S1.3: default to the safe no-op.
        self._llm = llm_extractor or NullLLMExtractor()
        self._scorer = scorer or ConfidenceScorer()
        self._ranker = ranker or AtomRanker()
        self._metrics = metrics or NullMetricsSink()

    def run(
        self,
        document: CanonicalDocument,
        context: Optional[ExtractionContext] = None,
    ) -> ExtractionResult:
        """Execute the cascade for a single canonical document."""
        start = time.perf_counter()

        # --- Validation (terminal failure path) ---
        try:
            validate_canonical_document(document)
        except MalformedDocumentError:
            self._metrics.increment(M_FAILURES_TOTAL, labels={"stage": "validate"})
            logger.warning("extraction.malformed_document")
            raise

        ctx = context or self._derive_context(document)
        # Ensure extractors can resolve the real documentId via metadata.
        ctx = self._with_document_id(ctx, document.documentId)

        self._metrics.increment(M_DOCUMENTS_TOTAL, labels={"tenant": ctx.tenantId})

        text = document.text or ""
        collected: List[Atom] = []

        # --- Stage 1: rule-based ---
        rule_atoms = self._run_stage(self._rule, text, ctx)
        collected.extend(rule_atoms)

        # --- Stage 2: heuristic (only when stage 1 found nothing) ---
        if not rule_atoms:
            collected.extend(self._run_stage(self._heuristic, text, ctx))

        # --- Stage 3: LLM (interface only; no calls in S1.3) ---
        # Intentionally not invoked. Wiring deferred to a future slice.

        # --- Confidence scoring ---
        for atom in collected:
            method = self._method_for(atom)
            self._scorer.apply(atom, method)

        # --- Ranking (dedup + merge) ---
        ranked = self._ranker.rank(collected)

        latency_ms = (time.perf_counter() - start) * 1000.0
        self._metrics.observe(M_LATENCY_MS, latency_ms, labels={"tenant": ctx.tenantId})
        self._metrics.increment(
            M_ATOMS_TOTAL, value=float(len(ranked)), labels={"tenant": ctx.tenantId}
        )

        return ExtractionResult(
            documentId=document.documentId,
            atoms=ranked,
            extractionVersion=EXTRACTION_VERSION,
            latencyMs=latency_ms,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _run_stage(self, extractor: Extractor, text: str, ctx: ExtractionContext) -> List[Atom]:
        """Invoke one cascade stage with fault isolation."""
        try:
            output = extractor.extract(text, ctx)
            return list(output.atoms)
        except Exception:  # noqa: BLE001 — isolate stage faults deliberately
            self._metrics.increment(
                M_FAILURES_TOTAL, labels={"stage": extractor.name}
            )
            logger.exception("extraction.stage_failed stage=%s", extractor.name)
            return []

    @staticmethod
    def _method_for(atom: Atom) -> ExtractionMethod:
        # Atoms carry no method field in the contract; the producing stage maps
        # confidence into bands, so we infer method from confidence value.
        # Rule-based >= 0.90; heuristic <= 0.80. (LLM never emits in S1.3.)
        if atom.confidence >= 0.90:
            return ExtractionMethod.RULE_BASED
        return ExtractionMethod.HEURISTIC

    @staticmethod
    def _derive_context(document: CanonicalDocument) -> ExtractionContext:
        meta = dict(getattr(document, "metadata", {}) or {})
        correlation_id = str(meta.get("correlationId") or document.documentId)
        return ExtractionContext(
            tenantId=document.tenantId,
            correlationId=correlation_id,
            documentMetadata=meta,
        )

    @staticmethod
    def _with_document_id(ctx: ExtractionContext, document_id: str) -> ExtractionContext:
        meta = dict(ctx.documentMetadata or {})
        meta["documentId"] = document_id
        return ExtractionContext(
            tenantId=ctx.tenantId,
            correlationId=ctx.correlationId,
            documentMetadata=meta,
        )
