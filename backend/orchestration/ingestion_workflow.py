"""
Ingestion workflow.

Coordinates the write path of PMOS:

    Connector -> Ingestion -> Extraction -> Resolution -> Indexing

Pulls raw documents from a source connector, ingests/normalises them, extracts
structured content, resolves entities/references, and finally indexes the
result so the query workflow can retrieve it. Emits INDEX_UPDATED on success so
caches and dashboards can invalidate/refresh.
"""

from __future__ import annotations

from .errors import InvalidWorkflowInput
from .event_bus import EventType
from .query_workflow import _call
from .workflow_context import WorkflowContext
from .workflow_engine import Step, Workflow

WORKFLOW_NAME = "ingestion"


def build_ingestion_workflow(deps) -> Workflow:
    def step_validate(ctx: WorkflowContext) -> None:
        source = ctx.payload.get("source") or ctx.payload.get("connector")
        if not source:
            raise InvalidWorkflowInput("ingestion requires a 'source'/'connector'")
        ctx.set("source", source)
        ctx.set("source_options", ctx.payload.get("options", {}))

    def step_connector(ctx: WorkflowContext) -> None:
        fetch = _call(deps.connectors, "fetch", "pull", "list_documents")
        raw_docs = fetch(ctx.require("source"), **ctx.get("source_options", {}))
        ctx.set("raw_documents", raw_docs)

    def step_ingestion(ctx: WorkflowContext) -> None:
        ingest = _call(deps.ingestion, "ingest", "normalize", "process")
        ctx.set("ingested", ingest(ctx.require("raw_documents")))

    def step_extraction(ctx: WorkflowContext) -> None:
        extract = _call(deps.extraction, "extract", "parse")
        ctx.set("extracted", extract(ctx.require("ingested")))

    def step_resolution(ctx: WorkflowContext) -> None:
        resolve = _call(deps.resolution, "resolve", "link", "dedupe")
        ctx.set("resolved", resolve(ctx.require("extracted")))

    def step_indexing(ctx: WorkflowContext) -> None:
        index = _call(deps.indexing, "index", "upsert", "write")
        result = index(ctx.require("resolved"))
        ctx.set("index_result", result)
        if deps.event_bus:
            deps.event_bus.publish(
                EventType.INDEX_UPDATED,
                job_id=ctx.job_id, workflow=WORKFLOW_NAME,
                source=ctx.get("source"),
            )

    def step_summary(ctx: WorkflowContext) -> None:
        docs = ctx.get("raw_documents") or []
        ctx.set("response", {
            "source": ctx.get("source"),
            "documents_ingested": len(docs) if hasattr(docs, "__len__") else None,
            "index_result": ctx.get("index_result"),
        })

    return Workflow(
        WORKFLOW_NAME,
        steps=[
            Step("validate", step_validate),
            Step("connector", step_connector),
            Step("ingestion", step_ingestion),
            Step("extraction", step_extraction),
            Step("resolution", step_resolution),
            Step("indexing", step_indexing),
            Step("summary", step_summary),
        ],
        default_timeout_s=getattr(deps, "ingestion_timeout_s", 600.0),
    )
