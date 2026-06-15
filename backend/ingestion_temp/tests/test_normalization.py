"""Normalization tests."""

import pytest

from backend.ingestion.contracts import CanonicalDocument
from backend.ingestion.normalization import registry, NormalizationError
from backend.ingestion.normalization.base import clean_text, strip_zero_width
from .fixtures import benign_zendesk_ticket


def test_zendesk_normalizes_to_canonical_document():
    doc = registry.normalize("zendesk", benign_zendesk_ticket(), connector_id="z1")
    assert isinstance(doc, CanonicalDocument)
    assert doc.title == "Cannot log into dashboard"
    assert "can't log into" in doc.body
    assert "Still broken" in doc.body  # comment folded in


def test_zendesk_preserves_provenance_and_identifiers():
    doc = registry.normalize("zendesk", benign_zendesk_ticket(), connector_id="z1")
    p = doc.provenance
    assert p.connector_id == "z1"
    assert p.connector_type == "zendesk"
    assert p.source_id == "1001"
    assert p.source_type == "ticket"
    assert p.source_url.endswith("1001.json")
    assert p.source_created_at == "2026-06-10T09:00:00Z"


def test_zendesk_preserves_source_metadata():
    doc = registry.normalize("zendesk", benign_zendesk_ticket(), connector_id="z1")
    md = doc.source_metadata
    assert md.get("status") == "open"
    assert md.get("priority") == "normal"
    assert md.get("tags") == ["login", "dashboard"]
    assert md.get("requester_id") == 555


def test_missing_id_raises_normalization_error():
    bad = benign_zendesk_ticket()
    del bad["id"]
    with pytest.raises(NormalizationError):
        registry.normalize("zendesk", bad, connector_id="z1")


def test_unknown_connector_type_raises():
    with pytest.raises(NormalizationError):
        registry.normalize("salesforce", {"id": 1}, connector_id="x")


def test_clean_text_strips_html_and_zero_width():
    dirty = "Hello\u200b <b>world</b>\r\n\r\n\r\nend"
    cleaned = clean_text(dirty)
    assert "<b>" not in cleaned
    assert "\u200b" not in cleaned
    assert "\r" not in cleaned


def test_strip_zero_width_removes_invisible_chars():
    assert strip_zero_width("a\u200b\u200c\u200db") == "ab"


def test_normalization_strips_hidden_chars_from_body():
    t = benign_zendesk_ticket()
    t["description"] = "Please\u200b help\u2060 me"
    doc = registry.normalize("zendesk", t, connector_id="z1")
    assert "\u200b" not in doc.body
    assert "\u2060" not in doc.body
