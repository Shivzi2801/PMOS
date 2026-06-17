"""
response_models.py
==================

Pydantic models describing every **outbound** payload.

Why this file exists
--------------------
Every PMOS response shares one envelope so that *all* clients (UI, partners,
internal tools) can parse responses with a single, predictable shape:

    {
      "request_id": "...",
      "timestamp":  "...",
      "status":     "success" | "error",
      "data":       { ... } | null,
      "errors":     [ { code, message, field? } ] | null
    }

Route handlers never build raw dicts. They return ``data`` payloads, and the
``ResponseEnvelope`` helpers wrap them. This guarantees that a malformed
response can't accidentally ship, and that the success/error contract is
identical across every endpoint and every API version.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResponseStatus(str, Enum):
    success = "success"
    error = "error"


class ErrorDetail(BaseModel):
    """One structured error. A response may carry several."""

    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable explanation.")
    field: Optional[str] = Field(
        default=None, description="Offending field for validation errors."
    )


class ResponseEnvelope(BaseModel, Generic[T]):
    """The universal PMOS response wrapper."""

    request_id: str
    timestamp: str = Field(default_factory=_now_iso)
    status: ResponseStatus
    data: Optional[T] = None
    errors: Optional[List[ErrorDetail]] = None

    # ------------------------------------------------------------------ #
    # Construction helpers used by route handlers and error handlers.
    # ------------------------------------------------------------------ #
    @classmethod
    def ok(cls, request_id: str, data: Any) -> "ResponseEnvelope":
        return cls(
            request_id=request_id,
            status=ResponseStatus.success,
            data=data,
            errors=None,
        )

    @classmethod
    def fail(
        cls, request_id: str, errors: List[ErrorDetail]
    ) -> "ResponseEnvelope":
        return cls(
            request_id=request_id,
            status=ResponseStatus.error,
            data=None,
            errors=errors,
        )


# --------------------------------------------------------------------------- #
# Endpoint-specific data payloads (placed inside envelope.data)
# --------------------------------------------------------------------------- #
class ConnectorRegisterData(BaseModel):
    connector_id: str
    name: str
    type: str
    status: str


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class IngestJobData(BaseModel):
    job_id: str
    status: JobStatus
    connector_id: str
    accepted_uris: int
    tracking: dict


class ProcessJobData(BaseModel):
    job_id: str
    status: JobStatus
    document_count: int
    tracking: dict


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    source_uri: Optional[str] = None
    snippet: Optional[str] = None
    score: Optional[float] = None


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float
    source_uri: Optional[str] = None


class SearchData(BaseModel):
    query: str
    chunks: List[RetrievedChunk]
    citations: List[Citation]
    total: int


class AnswerData(BaseModel):
    answer: str
    citations: List[Citation]
    confidence: float = Field(..., ge=0.0, le=1.0)
    verification_status: str
    metadata: dict


class GroundingData(BaseModel):
    verified: bool
    verification_status: str
    supported_claims: int
    unsupported_claims: int
    details: list


class HealthData(BaseModel):
    status: str
    dependencies: dict
    uptime_seconds: float


class VersionData(BaseModel):
    api_version: str
    current_major: str
    supported_majors: List[str]
    deprecated_majors: List[str]


class MetricsData(BaseModel):
    request_count: int
    error_count: int
    success_rate: float
    rate_limit_events: int
    endpoint_usage: dict
    latency_ms: dict
