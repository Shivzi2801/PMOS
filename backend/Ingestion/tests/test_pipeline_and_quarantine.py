"""Quarantine service and end-to-end pipeline tests (incl. DoD demo)."""

from backend.ingestion.contracts import Provenance, InjectionStatus, PIIType
from backend.ingestion.quarantine import QuarantineService, InMemoryQuarantineStore
from backend.ingestion.pipeline import IngestionPipeline, PipelineOutcome
from .fixtures import (
    benign_zendesk_ticket,
    pii_zendesk_ticket,
    injection_zendesk_ticket,
)


def _prov():
    return Provenance(
        connector_id="z1", connector_type="zendesk",
        source_id="1003", source_type="ticket",
    )


# --- Quarantine service ------------------------------------------------------

def test_quarantine_stores_and_retrieves():
    svc = QuarantineService(InMemoryQuarantineStore())
    rec = svc.quarantine(
        provenance=_prov(),
        reason="test reason",
        original_payload={"id": 1003, "secret": "x"},
    )
    fetched = svc.get(rec.quarantine_id)
    assert fetched is not None
    assert fetched.reason == "test reason"
    assert fetched.original_payload["id"] == 1003
    assert fetched.provenance.source_id == "1003"
    assert svc.count() == 1


# --- Pipeline: benign --------------------------------------------------------

def test_benign_ticket_is_published():
    pipe = IngestionPipeline()
    res = pipe.process(
        benign_zendesk_ticket(), connector_type="zendesk", connector_id="z1"
    )
    assert res.outcome == PipelineOutcome.PUBLISHED
    assert res.injection_finding.status == InjectionStatus.SAFE
    assert not res.pii_result.has_findings


# --- Pipeline: PII-only (no injection) --------------------------------------

def test_pii_ticket_published_and_redacted():
    pipe = IngestionPipeline()
    res = pipe.process(
        pii_zendesk_ticket(), connector_type="zendesk", connector_id="z1"
    )
    # Contains card, ssn, email, phone — none CRITICAL -> published, redacted.
    assert res.outcome == PipelineOutcome.PUBLISHED
    assert res.pii_result.has_findings
    body = res.document.body
    assert "4111 1111 1111 1111" not in body
    assert "123-45-6789" not in body
    assert "jane.doe@example.com" not in body
    assert "[REDACTED:" in body
    # Annotations attached
    assert "pii" in res.document.annotations
    assert "injection" in res.document.annotations


def test_critical_pii_alone_quarantines():
    ticket = benign_zendesk_ticket()
    ticket["description"] = "here is my key AKIAIOSFODNN7EXAMPLE"
    res = IngestionPipeline().process(
        ticket, connector_type="zendesk", connector_id="z1"
    )
    assert res.outcome == PipelineOutcome.QUARANTINED
    assert "critical_pii" in res.reason


# --- DoD DEMO: PII + injection ticket is normalized, flagged, quarantined ----

def test_dod_demo_injection_ticket_is_quarantined():
    svc = QuarantineService(InMemoryQuarantineStore())
    pipe = IngestionPipeline(quarantine_service=svc)

    res = pipe.process(
        injection_zendesk_ticket(), connector_type="zendesk", connector_id="z1"
    )

    # Normalized
    assert res.document is not None
    assert res.document.provenance.source_id == "1003"

    # Flagged: injection detected AND PII detected
    assert res.injection_finding.status == InjectionStatus.QUARANTINED
    assert len(res.injection_finding.categories) >= 2
    pii_types = {f.pii_type for f in res.pii_result.findings}
    assert PIIType.EMAIL in pii_types
    assert PIIType.API_KEY in pii_types
    assert PIIType.SSN in pii_types

    # Quarantined with provenance + original payload preserved
    assert res.outcome == PipelineOutcome.QUARANTINED
    assert res.quarantine_id is not None
    stored = svc.get(res.quarantine_id)
    assert stored is not None
    assert stored.original_payload["id"] == 1003
    assert stored.provenance.connector_type == "zendesk"
    assert "prompt_injection" in stored.reason
    assert stored.injection_finding is not None
    assert len(stored.pii_findings) >= 3


def test_failed_normalization_returns_failed_outcome():
    bad = {"subject": "no id"}  # missing required id
    res = IngestionPipeline().process(
        bad, connector_type="zendesk", connector_id="z1"
    )
    assert res.outcome == PipelineOutcome.FAILED
    assert res.errors
