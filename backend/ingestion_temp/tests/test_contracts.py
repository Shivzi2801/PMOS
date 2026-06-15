"""Unit tests for the contracts package."""

import pytest

from backend.ingestion.contracts import (
    CanonicalDocument,
    Provenance,
    SourceMetadata,
    PIIFinding,
    PIIType,
    PIISeverity,
    InjectionFinding,
    InjectionMatch,
    InjectionStatus,
    InjectionCategory,
    QuarantineRecord,
)


def _prov():
    return Provenance(
        connector_id="zendesk-prod",
        connector_type="zendesk",
        source_id="1",
        source_type="ticket",
    )


def test_canonical_document_computes_hash_and_preserves_provenance():
    doc = CanonicalDocument(title="t", body="b", provenance=_prov())
    assert doc.content_hash is not None
    assert doc.provenance.connector_id == "zendesk-prod"
    assert doc.document_id


def test_canonical_with_body_recomputes_hash_keeps_id():
    doc = CanonicalDocument(title="t", body="b", provenance=_prov())
    doc2 = doc.with_body("new body")
    assert doc2.document_id == doc.document_id
    assert doc2.content_hash != doc.content_hash


def test_pii_finding_validates_confidence():
    with pytest.raises(ValueError):
        PIIFinding(PIIType.EMAIL, PIISeverity.LOW, 1.5, 0, 1, "x", "email")


def test_pii_finding_validates_span():
    with pytest.raises(ValueError):
        PIIFinding(PIIType.EMAIL, PIISeverity.LOW, 0.9, 5, 2, "x", "email")


def test_severity_rank_ordering():
    assert PIISeverity.CRITICAL.rank > PIISeverity.HIGH.rank > PIISeverity.LOW.rank


def test_injection_finding_distinct_categories():
    f = InjectionFinding(
        status=InjectionStatus.QUARANTINED,
        score=1.2,
        matches=[
            InjectionMatch(InjectionCategory.INSTRUCTION_OVERRIDE, "a", 0.6, 0, 5, "x"),
            InjectionMatch(InjectionCategory.DATA_EXFILTRATION, "b", 0.6, 6, 9, "y"),
            InjectionMatch(InjectionCategory.INSTRUCTION_OVERRIDE, "c", 0.4, 10, 12, "z"),
        ],
    )
    assert set(f.categories) == {
        InjectionCategory.INSTRUCTION_OVERRIDE,
        InjectionCategory.DATA_EXFILTRATION,
    }


def test_quarantine_record_serializes_payload_and_provenance():
    rec = QuarantineRecord(
        provenance=_prov(),
        reason="bad",
        original_payload={"id": 1, "x": "y"},
    )
    d = rec.to_dict()
    assert d["reason"] == "bad"
    assert d["original_payload"]["id"] == 1
    assert d["provenance"]["connector_type"] == "zendesk"
    assert d["quarantine_id"]
