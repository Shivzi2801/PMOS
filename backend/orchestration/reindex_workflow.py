"""
Reindex workflow.

Handles updates to documents already known to PMOS:

    Document Update -> Delete Old Index -> Reprocess -> Reindex

Given one or more document ids, it removes their stale index entries, re-runs
extraction/resolution on the fresh content, and writes the new index entries.
This keeps retrieval consistent after source documents change without a full
re-ingestion of the entire corpus.

The delete step is treated as the critical ordering invariant: old entries are
removed before new ones are written so retrieval never sees duplicate/conflicting
versions of the same document mid-flight.
"""

from __future__ import annotations

from .errors import InvalidWorkflowInput
from .event_bus import EventType
from .query_workflow import _call
from .workflow_context import WorkflowContext
from .workflow_engine import Step, Workflow

WORKFLOW_NAME = "reindex"


def build_reindex_workflow(deps) -> Workflow:
    def step_validate(ctx: WorkflowContext) -> None:
        doc_ids = ctx.payload.get("document_ids") or ctx.payload.get("doc_ids")
        if not doc_ids:
            raise InvalidWorkflowInput("reindex requires 'document_ids'")
        ctx.set("document_ids", list(doc_ids))
        ctx.set("updated_content", ctx.payload.get("content"))

    def step_delete_old(ctx: WorkflowContext) -> None:
        delete = _call(deps.indexing, "delete", "remove", "delete_documents")
        ctx.set("deleted", delete(ctx.require("document_ids")))

    def step_reprocess(ctx: WorkflowContext) -> None:
        # Re-run extraction + resolution against updated content (or re-fetch).
        content = ctx.get("updated_content")
        if content is None:
            fetch = _call(deps.connectors, "fetch_by_id", "get_documents", "fetch")
            content = fetch(ctx.require("document_ids"))
        extract = _call(deps.extraction, "extract", "parse")
        extracted = extract(content)
        resolve = _call(deps.resolution, "resolve", "link", "dedupe")
        ctx.set("resolved", resolve(extracted))

    def step_reindex(ctx: WorkflowContext) -> None:
        index = _call(deps.indexing, "index", "upsert", "write")
        ctx.set("index_result", index(ctx.require("resolved")))
        if deps.event_bus:
            deps.event_bus.publish(
                EventType.INDEX_UPDATED,
                job_id=ctx.job_id, workflow=WORKFLOW_NAME,
                document_ids=ctx.get("document_ids"),
            )

    def step_summary(ctx: WorkflowContext) -> None:
        ctx.set("response", {
            "document_ids": ctx.get("document_ids"),
            "deleted": ctx.get("deleted"),
            "index_result": ctx.get("index_result"),
        })

    return Workflow(
        WORKFLOW_NAME,
        steps=[
            Step("validate", step_validate),
            Step("delete_old_index", step_delete_old),
            Step("reprocess", step_reprocess),
            Step("reindex", step_reindex),
            Step("summary", step_summary),
        ],
        default_timeout_s=getattr(deps, "reindex_timeout_s", 600.0),
    )
