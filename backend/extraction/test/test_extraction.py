"""Test suite for the F-10 Extraction Engine (Slice 1.3).

Covers:
  * successful fact extraction (rule-based and heuristic)
  * duplicate removal / equivalent-fact merge
  * confidence scoring bands
  * malformed document handling
  * empty document handling
  * observability metric emission
"""

from __future__ import annotations

import pytest

from backend.extraction.confidence.scorer import (
    ConfidenceScorer,
    ExtractionMethod,
)
from backend.extraction.contracts.atoms import FactAtom, SourceOffset
from backend.extraction.contracts.canonical_document import CanonicalDocumentModel
from backend.extraction.contracts.errors import (
    ConfidenceScoringError,
    MalformedDocumentError,
)
from backend.extraction.contracts.results import ExtractionContext
from backend.extraction.extractors.heuristic import HeuristicExtractor
from backend.extraction.extractors.llm import NullLLMExtractor
from backend.extraction.extractors.rule_based import RuleBasedExtractor
from backend.extraction.pipeline.metrics import (
    InMemoryMetricsSink,
    M_ATOMS_TOTAL,
    M_DOCUMENTS_TOTAL,
    M_FAILURES_TOTAL,
    M_LATENCY_MS,
)
from backend.extraction.pipeline.pipeline import ExtractionPipeline
from backend.extraction.ranking.ranker import AtomRanker


TENANT = "tenant_alpha"


def make_doc(text: str, doc_id: str = "doc_1", tenant: str = TENANT) -> CanonicalDocumentModel:
    return CanonicalDocumentModel(
        documentId=doc_id,
        tenantId=tenant,
        text=text,
        metadata={"correlationId": "corr_1", "source": "unit_test"},
    )


def make_context(tenant: str = TENANT) -> ExtractionContext:
    return ExtractionContext(
        tenantId=tenant,
        correlationId="corr_1",
        documentMetadata={"documentId": "doc_1"},
    )


# --------------------------------------------------------------------------- #
# Successful fact extraction
# --------------------------------------------------------------------------- #
def test_rule_based_extracts_upgraded_to():
    extractor = RuleBasedExtractor()
    out = extractor.extract("Acme Corp upgraded to Enterprise Plan.", make_context())
    assert len(out.atoms) == 1
    atom = out.atoms[0]
    assert atom.subject == "Acme Corp"
    assert atom.predicate == "upgraded_to"
    assert atom.object == "Enterprise Plan"
    assert atom.confidence >= 0.90
    assert atom.sourceOffsets and atom.sourceOffsets[0].end > atom.sourceOffsets[0].start


def test_rule_based_belongs_to_and_started_at():
    extractor = RuleBasedExtractor()
    text = "Project Orion belongs to Division X. Jane started at Globex."
    out = extractor.extract(text, make_context())
    preds = {(a.subject, a.predicate, a.object) for a in out.atoms}
    assert ("Project Orion", "belongs_to", "Division X") in preds
    assert ("Jane", "started_at", "Globex") in preds


def test_pipeline_successful_extraction_end_to_end():
    pipeline = ExtractionPipeline(metrics=InMemoryMetricsSink())
    result = pipeline.run(make_doc("Acme Corp upgraded to Enterprise Plan."))
    assert result.documentId == "doc_1"
    assert result.atom_count == 1
    assert result.atoms[0].predicate == "upgraded_to"
    assert result.latencyMs >= 0.0
    assert result.extractionVersion


def test_heuristic_fallback_when_rules_empty():
    # No rule template matches, but heuristic verb "joined" does.
    pipeline = ExtractionPipeline()
    result = pipeline.run(make_doc("Maria Lopez joined Initech as lead engineer."))
    assert result.atom_count >= 1
    atom = result.atoms[0]
    assert 0.60 <= atom.confidence <= 0.80
    assert atom.predicate == "joined"


def test_rule_based_preferred_over_heuristic():
    # When stage 1 matches, stage 2 must not run.
    pipeline = ExtractionPipeline()
    result = pipeline.run(make_doc("Acme Corp upgraded to Enterprise Plan."))
    assert all(a.confidence >= 0.90 for a in result.atoms)


# --------------------------------------------------------------------------- #
# Duplicate removal / merge
# --------------------------------------------------------------------------- #
def test_ranker_removes_duplicates_keeps_highest_confidence():
    ranker = AtomRanker()
    low = FactAtom(
        tenantId=TENANT, documentId="doc_1", subject="Acme", predicate="upgraded_to",
        object="Pro", confidence=0.65, sourceDocumentId="doc_1",
        sourceOffsets=(SourceOffset(0, 10),),
    )
    high = FactAtom(
        tenantId=TENANT, documentId="doc_1", subject="acme", predicate="UPGRADED_TO",
        object="pro", confidence=0.93, sourceDocumentId="doc_1",
        sourceOffsets=(SourceOffset(40, 55),),
    )
    ranked = ranker.rank([low, high])
    assert len(ranked) == 1
    assert ranked[0].confidence == 0.93
    # Provenance from both duplicates is preserved.
    spans = {o.as_tuple() for o in ranked[0].sourceOffsets}
    assert (0, 10) in spans and (40, 55) in spans


def test_ranker_keeps_distinct_facts():
    ranker = AtomRanker()
    a = FactAtom(
        tenantId=TENANT, documentId="doc_1", subject="Acme", predicate="upgraded_to",
        object="Pro", confidence=0.92, sourceDocumentId="doc_1",
    )
    b = FactAtom(
        tenantId=TENANT, documentId="doc_1", subject="Globex", predicate="acquired",
        object="Initech", confidence=0.91, sourceDocumentId="doc_1",
    )
    ranked = ranker.rank([a, b])
    assert len(ranked) == 2
    # Sorted by descending confidence.
    assert ranked[0].confidence >= ranked[1].confidence


def test_pipeline_dedup_on_repeated_sentence():
    pipeline = ExtractionPipeline()
    text = "Acme Corp upgraded to Enterprise Plan. Acme Corp upgraded to Enterprise Plan."
    result = pipeline.run(make_doc(text))
    assert result.atom_count == 1


# --------------------------------------------------------------------------- #
# Confidence scoring
# --------------------------------------------------------------------------- #
def test_confidence_bands():
    scorer = ConfidenceScorer()
    assert scorer.bounds(ExtractionMethod.RULE_BASED) == (0.90, 1.00)
    assert scorer.bounds(ExtractionMethod.HEURISTIC) == (0.60, 0.80)
    assert scorer.bounds(ExtractionMethod.LLM) == (0.70, 0.95)


def test_confidence_clamps_out_of_band():
    scorer = ConfidenceScorer()
    assert scorer.score(ExtractionMethod.HEURISTIC, 0.99) == 0.80
    assert scorer.score(ExtractionMethod.RULE_BASED, 0.10) == 0.90


def test_confidence_default_used_when_no_signal():
    scorer = ConfidenceScorer()
    assert scorer.score(ExtractionMethod.HEURISTIC) == 0.65
    assert scorer.score(ExtractionMethod.RULE_BASED) == 0.92


def test_confidence_invalid_signal_raises():
    scorer = ConfidenceScorer()
    with pytest.raises(ConfidenceScoringError):
        scorer.score(ExtractionMethod.RULE_BASED, 1.5)


def test_confidence_apply_normalizes_atom():
    scorer = ConfidenceScorer()
    atom = FactAtom(
        tenantId=TENANT, documentId="doc_1", subject="A", predicate="is_a",
        object="B", confidence=0.50, sourceDocumentId="doc_1",
    )
    scorer.apply(atom, ExtractionMethod.RULE_BASED)
    assert atom.confidence == 0.90


# --------------------------------------------------------------------------- #
# Malformed document handling
# --------------------------------------------------------------------------- #
def test_pipeline_none_document_raises_and_counts_failure():
    metrics = InMemoryMetricsSink()
    pipeline = ExtractionPipeline(metrics=metrics)
    with pytest.raises(MalformedDocumentError):
        pipeline.run(None)  # type: ignore[arg-type]
    assert metrics.counter_value(M_FAILURES_TOTAL, {"stage": "validate"}) == 1.0


def test_pipeline_missing_field_raises():
    class Broken:
        documentId = "x"
        tenantId = "t"
        # missing text and metadata

    pipeline = ExtractionPipeline()
    with pytest.raises(MalformedDocumentError):
        pipeline.run(Broken())  # type: ignore[arg-type]


def test_factatom_requires_complete_triple():
    with pytest.raises(ValueError):
        FactAtom(
            tenantId=TENANT, documentId="doc_1", subject="", predicate="x",
            object="y", confidence=0.9, sourceDocumentId="doc_1",
        )


def test_extractor_fault_is_isolated():
    class Exploding(RuleBasedExtractor):
        def extract(self, text, context):
            raise RuntimeError("boom")

    metrics = InMemoryMetricsSink()
    pipeline = ExtractionPipeline(rule_extractor=Exploding(), metrics=metrics)
    # Heuristic still runs because rule stage returned nothing (due to fault).
    result = pipeline.run(make_doc("Maria Lopez joined Initech."))
    assert metrics.counter_value(M_FAILURES_TOTAL, {"stage": "rule_based"}) == 1.0
    assert result.atom_count >= 1


# --------------------------------------------------------------------------- #
# Empty document handling
# --------------------------------------------------------------------------- #
def test_pipeline_empty_text_returns_no_atoms():
    metrics = InMemoryMetricsSink()
    pipeline = ExtractionPipeline(metrics=metrics)
    result = pipeline.run(make_doc(""))
    assert result.atom_count == 0
    assert metrics.counter_value(M_DOCUMENTS_TOTAL, {"tenant": TENANT}) == 1.0
    assert metrics.counter_value(M_ATOMS_TOTAL, {"tenant": TENANT}) == 0.0


def test_pipeline_whitespace_only_returns_no_atoms():
    pipeline = ExtractionPipeline()
    result = pipeline.run(make_doc("    \n\t  "))
    assert result.atom_count == 0


def test_empty_text_is_valid_not_malformed():
    # Empty text is a valid (if uninteresting) document, not a malformed one.
    pipeline = ExtractionPipeline()
    result = pipeline.run(make_doc(""))
    assert result.documentId == "doc_1"


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #
def test_metrics_emitted_on_success():
    metrics = InMemoryMetricsSink()
    pipeline = ExtractionPipeline(metrics=metrics)
    pipeline.run(make_doc("Acme Corp upgraded to Enterprise Plan."))
    assert metrics.counter_value(M_DOCUMENTS_TOTAL, {"tenant": TENANT}) == 1.0
    assert metrics.counter_value(M_ATOMS_TOTAL, {"tenant": TENANT}) == 1.0
    assert metrics.observation_count(M_LATENCY_MS, {"tenant": TENANT}) == 1


# --------------------------------------------------------------------------- #
# LLM stage is interface-only
# --------------------------------------------------------------------------- #
def test_null_llm_extractor_emits_nothing():
    out = NullLLMExtractor().extract("anything at all", make_context())
    assert out.atoms == []
    assert out.method == ExtractionMethod.LLM
