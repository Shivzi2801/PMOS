"""
Query workflow.

Coordinates the read path of PMOS:

    Question -> Retrieval -> Context Assembly -> Generation -> Grounding -> Response

Each step is a thin adapter that calls into a previously-built slice
(backend/retrieval, backend/context, backend/generation, backend/grounding) and
writes its output into the shared WorkflowContext for the next step to consume.

The adapters are defensive: they accept either an object exposing the expected
method or a duck-typed callable, and they validate that the prior step produced
the inputs they need. This is what makes the orchestration layer the single
place where cross-slice contracts are enforced.
"""

from __future__ import annotations

from .errors import InvalidWorkflowInput
from .event_bus import EventType
from .workflow_context import WorkflowContext
from .workflow_engine import Step, Workflow

WORKFLOW_NAME = "query"


def _call(dep, *method_names):
    """Resolve the first available bound method from a dependency object."""
    for m in method_names:
        fn = getattr(dep, m, None)
        if callable(fn):
            return fn
    if callable(dep):
        return dep
    raise InvalidWorkflowInput(
        f"dependency {dep!r} exposes none of {method_names}"
    )


def build_query_workflow(deps) -> Workflow:
    def step_validate(ctx: WorkflowContext) -> None:
        question = ctx.payload.get("question") or ctx.payload.get("query")
        if not question or not str(question).strip():
            raise InvalidWorkflowInput("query workflow requires a 'question'")
        ctx.set("question", question)
        ctx.set("top_k", ctx.payload.get("top_k", 10))
        ctx.set("filters", ctx.payload.get("filters", {}))

    def step_retrieval(ctx: WorkflowContext) -> None:
        retrieve = _call(deps.retrieval, "retrieve", "search", "query")
        results = retrieve(
            ctx.require("question"),
            top_k=ctx.get("top_k"),
            filters=ctx.get("filters"),
        )
        ctx.set("retrieved", results)

    def step_context(ctx: WorkflowContext) -> None:
        assemble = _call(deps.context, "assemble", "build", "build_context")
        assembled = assemble(
            question=ctx.require("question"),
            retrieved=ctx.require("retrieved"),
        )
        ctx.set("assembled_context", assembled)

    def step_generation(ctx: WorkflowContext) -> None:
        generate = _call(deps.generation, "generate", "complete", "answer")
        answer = generate(
            question=ctx.require("question"),
            context=ctx.require("assembled_context"),
        )
        ctx.set("answer", answer)
        if deps.event_bus:
            deps.event_bus.publish(
                EventType.ANSWER_GENERATED,
                job_id=ctx.job_id, workflow=WORKFLOW_NAME,
            )

    def step_grounding(ctx: WorkflowContext) -> None:
        ground = _call(deps.grounding, "ground", "verify", "attribute")
        grounded = ground(
            answer=ctx.require("answer"),
            context=ctx.require("assembled_context"),
        )
        ctx.set("grounded_answer", grounded)

    def step_response(ctx: WorkflowContext) -> None:
        ctx.set("response", {
            "question": ctx.require("question"),
            "answer": ctx.get("grounded_answer", ctx.get("answer")),
            "citations": ctx.get("grounded_answer", {}).get("citations")
            if isinstance(ctx.get("grounded_answer"), dict) else None,
            "retrieved_count": len(ctx.get("retrieved") or []),
        })

    return Workflow(
        WORKFLOW_NAME,
        steps=[
            Step("validate", step_validate),
            Step("retrieval", step_retrieval),
            Step("context_assembly", step_context),
            Step("generation", step_generation),
            Step("grounding", step_grounding),
            Step("response", step_response),
        ],
        default_timeout_s=getattr(deps, "query_timeout_s", 60.0),
    )
