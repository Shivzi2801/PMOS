"""PII detection tests."""

from backend.ingestion.contracts import PIIType, PIISeverity, RedactionMode
from backend.ingestion.pii import PIIEngine
from backend.ingestion.pii.detectors import (
    EmailDetector,
    SSNDetector,
    CreditCardDetector,
    APIKeyDetector,
    AccessTokenDetector,
    _luhn_ok,
)


def _types(result):
    return {f.pii_type for f in result.findings}


def test_detects_email():
    r = PIIEngine().scan("contact me at jane.doe@example.com please")
    assert PIIType.EMAIL in _types(r)


def test_detects_phone():
    r = PIIEngine().scan("call +1 (415) 555-0199 today")
    assert PIIType.PHONE in _types(r)


def test_detects_valid_ssn_only():
    r = PIIEngine().scan("ssn 123-45-6789 but not 000-12-3456")
    ssns = [f for f in r.findings if f.pii_type == PIIType.SSN]
    assert len(ssns) == 1


def test_detects_credit_card_with_luhn():
    r = PIIEngine().scan("card 4111 1111 1111 1111 on file")
    assert PIIType.CREDIT_CARD in _types(r)


def test_rejects_non_luhn_card():
    # 16 digits but fails Luhn
    r = PIIEngine().scan("number 1234 5678 1234 5671")
    assert PIIType.CREDIT_CARD not in _types(r)


def test_luhn_helper():
    assert _luhn_ok("4111111111111111")
    assert not _luhn_ok("4111111111111112")


def test_detects_aws_api_key_as_critical():
    r = PIIEngine().scan("key AKIAIOSFODNN7EXAMPLE here")
    api = [f for f in r.findings if f.pii_type == PIIType.API_KEY]
    assert api and api[0].severity == PIISeverity.CRITICAL


def test_detects_jwt_access_token_as_critical():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123456"
    r = PIIEngine().scan(f"token: {jwt}")
    tok = [f for f in r.findings if f.pii_type == PIIType.ACCESS_TOKEN]
    assert tok and tok[0].severity == PIISeverity.CRITICAL


def test_findings_have_confidence_in_range():
    r = PIIEngine().scan("email a@b.com card 4111111111111111")
    assert all(0.0 <= f.confidence <= 1.0 for f in r.findings)


def test_redact_mode_replaces_spans():
    r = PIIEngine().scan(
        "email jane@example.com ssn 123-45-6789", mode=RedactionMode.REDACT
    )
    assert r.redacted_text is not None
    assert "jane@example.com" not in r.redacted_text
    assert "123-45-6789" not in r.redacted_text
    assert "[REDACTED:EMAIL]" in r.redacted_text
    assert "[REDACTED:SSN]" in r.redacted_text


def test_annotate_mode_leaves_text_unchanged():
    r = PIIEngine().scan("email jane@example.com", mode=RedactionMode.ANNOTATE)
    assert r.redacted_text is None
    assert r.has_findings


def test_summary_counts_and_max_severity():
    text = "AKIAIOSFODNN7EXAMPLE and email a@b.com"
    r = PIIEngine().scan(text)
    summary = r.to_summary()
    assert summary["max_severity"] == "CRITICAL"
    assert summary["count"] >= 2


def test_overlapping_spans_deduped():
    # access_token generic pattern and bearer could both match; ensure no dup spans
    text = "Authorization: Bearer abcdefghijklmnop1234567890"
    r = PIIEngine().scan(text)
    spans = [(f.start, f.end) for f in r.findings]
    assert len(spans) == len(set(spans))


def test_clean_text_no_false_positive_on_plain_words():
    r = PIIEngine().scan("the quick brown fox jumps over the lazy dog")
    assert not r.has_findings
