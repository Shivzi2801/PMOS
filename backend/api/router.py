"""
router.py
=========

The v1 business router. Maps each REST endpoint to a request model, the
service adapter, and the standard response envelope.

Why this file exists
--------------------
This is where the API's *routing* responsibility lives. Each handler:

1. receives an already-validated request model (FastAPI did validation),
2. obtains the authenticated principal (stub for now),
3. delegates to ``PMOSServices`` (which fans out to the backend modules),
4. wraps the result in a ``ResponseEnvelope``.

Handlers contain no business logic and no I/O of their own — that keeps the
API thin and makes the backend modules the single source of behavior. The
router is mounted by ``app.py`` under the version prefix (``/api/v1``), so the
same handlers can later be mounted under ``/api/v2`` if v2 reuses them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .auth_stub import AuthProvider, Principal, get_auth_provider
from .request_models import (
    AnswerRequest,
    ConnectorRegisterRequest,
    DocumentIngestRequest,
    DocumentProcessRequest,
    GroundingVerifyRequest,
    SearchRequest,
)
from .response_models import ResponseEnvelope
from .services import PMOSServices, get_services

router = APIRouter(tags=["pmos"])


def _principal(request: Request, auth: AuthProvider = Depends(get_auth_provider)) -> Principal:
    """Resolve the calling principal (anonymous in S2.1)."""
    return auth.authenticate(request.headers)


# --------------------------------------------------------------------------- #
# Connectors
# --------------------------------------------------------------------------- #
@router.post("/connectors", response_model=ResponseEnvelope)
def register_connector(
    request: Request,
    body: ConnectorRegisterRequest,
    services: PMOSServices = Depends(get_services),
    principal: Principal = Depends(_principal),
):
    result = services.register_connector(
        name=body.name, type_=body.type.value, config=body.config
    )
    return ResponseEnvelope.ok(request.state.request_id, result)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
@router.post("/documents/ingest", response_model=ResponseEnvelope)
def ingest_documents(
    request: Request,
    body: DocumentIngestRequest,
    services: PMOSServices = Depends(get_services),
    principal: Principal = Depends(_principal),
):
    result = services.ingest(
        connector_id=body.connector_id,
        source_uris=body.source_uris,
        incremental=body.incremental,
    )
    return ResponseEnvelope.ok(request.state.request_id, result)


@router.post("/documents/process", response_model=ResponseEnvelope)
def process_documents(
    request: Request,
    body: DocumentProcessRequest,
    services: PMOSServices = Depends(get_services),
    principal: Principal = Depends(_principal),
):
    result = services.process(
        document_ids=body.document_ids, force_reindex=body.force_reindex
    )
    return ResponseEnvelope.ok(request.state.request_id, result)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
@router.post("/search", response_model=ResponseEnvelope)
def search(
    request: Request,
    body: SearchRequest,
    services: PMOSServices = Depends(get_services),
    principal: Principal = Depends(_principal),
):
    result = services.search(
        query=body.query,
        filters=body.filters.model_dump() if body.filters else None,
        retrieval=body.retrieval.model_dump(),
    )
    return ResponseEnvelope.ok(request.state.request_id, result)


# --------------------------------------------------------------------------- #
# Answer
# --------------------------------------------------------------------------- #
@router.post("/answer", response_model=ResponseEnvelope)
def answer(
    request: Request,
    body: AnswerRequest,
    services: PMOSServices = Depends(get_services),
    principal: Principal = Depends(_principal),
):
    result = services.answer(
        question=body.question,
        filters=body.filters.model_dump() if body.filters else None,
        retrieval=body.retrieval.model_dump(),
        verify=body.verify,
    )
    return ResponseEnvelope.ok(request.state.request_id, result)


# --------------------------------------------------------------------------- #
# Grounding
# --------------------------------------------------------------------------- #
@router.post("/grounding/verify", response_model=ResponseEnvelope)
def verify_grounding(
    request: Request,
    body: GroundingVerifyRequest,
    services: PMOSServices = Depends(get_services),
    principal: Principal = Depends(_principal),
):
    result = services.verify(
        answer=body.answer, citations=body.citations, question=body.question
    )
    return ResponseEnvelope.ok(request.state.request_id, result)
