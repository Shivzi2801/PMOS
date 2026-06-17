"""
request_models.py
=================

Pydantic models describing every **inbound** payload accepted by the API.

Why this file exists
--------------------
The API layer's first job after receiving a request is *validation*. Instead
of scattering ad-hoc ``if`` checks across route handlers, every endpoint
declares a typed request model here. FastAPI then validates the incoming JSON
against the model automatically and rejects malformed payloads with a 422
before any business logic runs. This gives us:

* a single, self-documenting description of the contract,
* automatic OpenAPI schema generation for the future UI / partners,
* consistent, centralized validation behavior.

Each model maps to exactly one endpoint. Field constraints (lengths, ranges,
required vs optional) encode the business rules for that endpoint.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Shared sub-models
# --------------------------------------------------------------------------- #
class RetrievalParams(BaseModel):
    """Tuning knobs forwarded to the retrieval module."""

    top_k: int = Field(
        default=8, ge=1, le=100,
        description="Maximum number of chunks to retrieve.",
    )
    min_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Minimum similarity score a chunk must meet to be returned.",
    )
    rerank: bool = Field(
        default=True,
        description="Whether retrieval should apply the reranking stage.",
    )


class SearchFilters(BaseModel):
    """Optional metadata filters narrowing retrieval/search scope."""

    connector_ids: Optional[List[str]] = Field(
        default=None, description="Restrict to documents from these connectors."
    )
    document_ids: Optional[List[str]] = Field(
        default=None, description="Restrict to these specific documents."
    )
    tags: Optional[List[str]] = Field(
        default=None, description="Restrict to documents carrying these tags."
    )
    created_after: Optional[str] = Field(
        default=None, description="ISO-8601 lower bound on document creation time."
    )
    created_before: Optional[str] = Field(
        default=None, description="ISO-8601 upper bound on document creation time."
    )


# --------------------------------------------------------------------------- #
# Connectors
# --------------------------------------------------------------------------- #
class ConnectorType(str, Enum):
    confluence = "confluence"
    notion = "notion"
    jira = "jira"
    gdrive = "gdrive"
    s3 = "s3"
    upload = "upload"


class ConnectorRegisterRequest(BaseModel):
    """Register a new source connector. -> connectors module."""

    name: str = Field(..., min_length=1, max_length=128)
    type: ConnectorType
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Connector-specific configuration (URLs, scopes, etc.).",
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
class DocumentIngestRequest(BaseModel):
    """Trigger the ingestion pipeline for a connector or explicit sources."""

    connector_id: str = Field(..., min_length=1)
    source_uris: Optional[List[str]] = Field(
        default=None,
        description="Specific URIs to ingest. If omitted, the connector is "
                    "fully synced.",
    )
    incremental: bool = Field(
        default=True,
        description="If true, only ingest items changed since the last sync.",
    )


class DocumentProcessRequest(BaseModel):
    """Run extraction -> resolution -> indexing for already-ingested docs."""

    document_ids: List[str] = Field(..., min_length=1)
    force_reindex: bool = Field(
        default=False,
        description="Re-run the full pipeline even if already indexed.",
    )


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
class SearchRequest(BaseModel):
    """Semantic search over indexed content. -> retrieval module."""

    query: str = Field(..., min_length=1, max_length=4096)
    filters: Optional[SearchFilters] = None
    retrieval: RetrievalParams = Field(default_factory=RetrievalParams)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v


# --------------------------------------------------------------------------- #
# Answer
# --------------------------------------------------------------------------- #
class AnswerRequest(BaseModel):
    """
    Full RAG question-answering request.

    Flows through retrieval -> context -> generation -> grounding.
    """

    question: str = Field(..., min_length=1, max_length=4096)
    filters: Optional[SearchFilters] = None
    retrieval: RetrievalParams = Field(default_factory=RetrievalParams)
    verify: bool = Field(
        default=True,
        description="Run the grounding/verification stage on the answer.",
    )

    @field_validator("question")
    @classmethod
    def _strip_question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v


# --------------------------------------------------------------------------- #
# Grounding
# --------------------------------------------------------------------------- #
class GroundingVerifyRequest(BaseModel):
    """Verify that an answer is supported by its cited evidence."""

    answer: str = Field(..., min_length=1)
    citations: List[str] = Field(
        ..., min_length=1,
        description="Chunk/document identifiers the answer claims to rely on.",
    )
    question: Optional[str] = Field(
        default=None,
        description="Original question, used for relevance checking.",
    )
