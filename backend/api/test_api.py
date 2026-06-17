"""
test_api.py
===========

Test suite for the S2.1 API layer.

Covers (per spec):
* health endpoint
* answer endpoint
* search endpoint
* validation
* error handling
* versioning
* rate limiting

Run with:  pytest backend/api/test_api.py -v

The tests use FastAPI's TestClient and a fresh app per module. Rate-limit tests
install a deliberately tiny limiter so limits can be hit in a few calls without
slowing the suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api import rate_limiter
from backend.api.api_metrics import get_metrics_registry


@pytest.fixture()
def client():
    app = create_app()
    get_metrics_registry().reset()
    with TestClient(app) as c:
        yield c


def _envelope_ok(body):
    assert set(["request_id", "timestamp", "status", "data", "errors"]).issubset(body)
    assert body["request_id"]
    assert body["timestamp"]


# --------------------------------------------------------------------------- #
# Health / version / metrics
# --------------------------------------------------------------------------- #
def test_health_endpoint(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    _envelope_ok(body)
    assert body["status"] == "success"
    assert body["data"]["status"] in ("ok", "degraded")
    assert "dependencies" in body["data"]
    # All nine backend modules must be reported.
    assert len(body["data"]["dependencies"]) == 9
    assert "X-Request-ID" in r.headers


def test_version_endpoint(client):
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    body = r.json()
    _envelope_ok(body)
    assert body["data"]["current_major"] == "v1"
    assert "v1" in body["data"]["supported_majors"]


def test_metrics_endpoint_tracks_requests(client):
    client.get("/api/v1/health")
    client.get("/api/v1/version")
    r = client.get("/api/v1/metrics")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["request_count"] >= 2
    assert "endpoint_usage" in data
    assert "latency_ms" in data
    assert 0.0 <= data["success_rate"] <= 1.0


# --------------------------------------------------------------------------- #
# Answer endpoint
# --------------------------------------------------------------------------- #
def test_answer_endpoint(client):
    r = client.post("/api/v1/answer", json={"question": "What is PMOS?"})
    assert r.status_code == 200
    body = r.json()
    _envelope_ok(body)
    data = body["data"]
    assert "answer" in data
    assert "citations" in data
    assert 0.0 <= data["confidence"] <= 1.0
    assert "verification_status" in data
    assert "metadata" in data


def test_answer_without_verification(client):
    r = client.post("/api/v1/answer", json={"question": "X?", "verify": False})
    assert r.status_code == 200
    assert r.json()["data"]["verification_status"] == "not_verified"


# --------------------------------------------------------------------------- #
# Search endpoint
# --------------------------------------------------------------------------- #
def test_search_endpoint(client):
    r = client.post(
        "/api/v1/search",
        json={"query": "roadmap priorities", "retrieval": {"top_k": 3}},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["query"] == "roadmap priorities"
    assert isinstance(data["chunks"], list)
    assert isinstance(data["citations"], list)
    assert "total" in data


# --------------------------------------------------------------------------- #
# Other business endpoints
# --------------------------------------------------------------------------- #
def test_connector_registration(client):
    r = client.post(
        "/api/v1/connectors",
        json={"name": "Eng Wiki", "type": "confluence", "config": {"space": "ENG"}},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["connector_id"].startswith("conn_")
    assert data["status"] == "registered"


def test_ingest_returns_job(client):
    r = client.post(
        "/api/v1/documents/ingest",
        json={"connector_id": "conn_123", "source_uris": ["a", "b"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["job_id"].startswith("job_")
    assert data["status"] == "queued"
    assert data["accepted_uris"] == 2
    assert "tracking" in data


def test_grounding_verify(client):
    r = client.post(
        "/api/v1/grounding/verify",
        json={"answer": "PMOS unifies PM knowledge.", "citations": ["chunk_0"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["verified"] is True
    assert data["supported_claims"] == 1


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_validation_missing_field(client):
    r = client.post("/api/v1/answer", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "error"
    assert body["errors"][0]["code"] == "validation_error"


def test_validation_blank_question(client):
    r = client.post("/api/v1/answer", json={"question": "   "})
    assert r.status_code == 422
    assert r.json()["status"] == "error"


def test_validation_top_k_out_of_range(client):
    r = client.post(
        "/api/v1/search", json={"query": "x", "retrieval": {"top_k": 9999}}
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_unknown_route_returns_envelope(client):
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["status"] == "error"
    assert body["errors"][0]["code"] == "http_error"


def test_pipeline_error_mapped(client, monkeypatch):
    from backend.api.services import get_services
    from backend.api.error_handlers import PipelineError

    def boom(*args, **kwargs):
        raise PipelineError("retrieval", "vector store unreachable")

    monkeypatch.setattr(get_services(), "search", boom)
    r = client.post("/api/v1/search", json={"query": "x"})
    assert r.status_code == 502
    body = r.json()
    assert body["errors"][0]["code"] == "pipeline_error"
    assert body["errors"][0]["field"] == "retrieval"


# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #
def test_v1_prefix_present(client):
    assert client.get("/api/v1/health").status_code == 200


def test_root_lists_versions(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "v1" in r.json()["supported_versions"]


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
def test_rate_limiting_triggers_429():
    # Tiny limiter: 2 requests / 60s on the answer scope.
    tiny = rate_limiter.InMemoryRateLimiter(
        default_policy=rate_limiter.RateLimitPolicy(limit=1000, window=60),
        scope_policies={"answer": rate_limiter.RateLimitPolicy(limit=2, window=60)},
    )
    rate_limiter.set_rate_limiter(tiny)
    try:
        app = create_app()
        get_metrics_registry().reset()
        with TestClient(app) as c:
            assert c.post("/api/v1/answer", json={"question": "a"}).status_code == 200
            assert c.post("/api/v1/answer", json={"question": "b"}).status_code == 200
            r = c.post("/api/v1/answer", json={"question": "c"})
            assert r.status_code == 429
            assert r.json()["errors"][0]["code"] == "rate_limit_exceeded"
            assert "Retry-After" in r.headers
            # Metrics should record the limit event.
            m = c.get("/api/v1/metrics").json()["data"]
            assert m["rate_limit_events"] >= 1
    finally:
        # Restore a permissive limiter for other tests.
        rate_limiter.set_rate_limiter(
            rate_limiter.InMemoryRateLimiter(
                default_policy=rate_limiter.RateLimitPolicy(limit=100000, window=60)
            )
        )


def test_health_not_rate_limited():
    tiny = rate_limiter.InMemoryRateLimiter(
        default_policy=rate_limiter.RateLimitPolicy(limit=1, window=60)
    )
    rate_limiter.set_rate_limiter(tiny)
    try:
        app = create_app()
        with TestClient(app) as c:
            for _ in range(5):
                assert c.get("/api/v1/health").status_code == 200
    finally:
        rate_limiter.set_rate_limiter(
            rate_limiter.InMemoryRateLimiter(
                default_policy=rate_limiter.RateLimitPolicy(limit=100000, window=60)
            )
        )
