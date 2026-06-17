"""
Tests for the PMOS orchestration layer.

Run with:  pytest backend/orchestration/test_orchestration.py -q

Covers: query/ingestion/reindex workflows, retry logic, state transitions,
job management, and workflow failures. All slice dependencies are replaced with
fakes so tests are hermetic and fast (retry sleeps are stubbed to no-op).
"""

from __future__ import annotations

import pytest

from backend.orchestration import (
    Orchestrator, Dependencies, RetryPolicy, WorkflowState, EventType,
    StateMachine, EventBus,
)
from backend.orchestration.errors import (
    TransientModuleError, InvalidStateTransition, InvalidWorkflowInput,
)


# --------------------------------------------------------------------------- #
# Fakes for previous slices
# --------------------------------------------------------------------------- #
class FakeRetrieval:
    def __init__(self): self.calls = 0
    def retrieve(self, q, top_k=10, filters=None):
        self.calls += 1
        return [{"id": "d1", "text": "alpha"}, {"id": "d2", "text": "beta"}]


class FakeContext:
    def assemble(self, question, retrieved):
        return {"prompt": question, "chunks": retrieved}


class FakeGeneration:
    def generate(self, question, context):
        return {"text": f"answer to: {question}"}


class FakeGrounding:
    def ground(self, answer, context):
        return {"text": answer["text"], "citations": ["d1"]}


class FakeConnectors:
    def fetch(self, source, **opts): return [{"id": "d1"}, {"id": "d2"}]
    def fetch_by_id(self, ids): return [{"id": i} for i in ids]


class FakeIngestion:
    def ingest(self, docs): return [{**d, "ingested": True} for d in docs]


class FakeExtraction:
    def extract(self, docs): return [{"id": d.get("id"), "fields": {}} for d in docs]


class FakeResolution:
    def resolve(self, docs): return docs


class FakeIndexing:
    def __init__(self): self.indexed = []; self.deleted = []
    def index(self, docs): self.indexed.extend(docs); return {"indexed": len(docs)}
    def delete(self, ids): self.deleted.extend(ids); return {"deleted": len(ids)}


def make_deps(**overrides):
    deps = Dependencies(
        connectors=FakeConnectors(),
        ingestion=FakeIngestion(),
        extraction=FakeExtraction(),
        resolution=FakeResolution(),
        indexing=FakeIndexing(),
        retrieval=FakeRetrieval(),
        context=FakeContext(),
        generation=FakeGeneration(),
        grounding=FakeGrounding(),
    )
    for k, v in overrides.items():
        setattr(deps, k, v)
    return deps


def make_orch(deps=None, **kw):
    # sleep stubbed so retries are instant
    return Orchestrator(deps or make_deps(), sleep=lambda s: None, **kw)


# --------------------------------------------------------------------------- #
# Query workflow
# --------------------------------------------------------------------------- #
def test_query_workflow_success():
    orch = make_orch()
    res = orch.run_query("what is the roadmap?")
    assert res.success
    assert res.state == WorkflowState.COMPLETED
    assert res.output["answer"]["text"].startswith("answer to:")
    assert res.output["retrieved_count"] == 2
    assert [s.name for s in res.steps] == [
        "validate", "retrieval", "context_assembly",
        "generation", "grounding", "response",
    ]


def test_query_workflow_rejects_empty_question():
    orch = make_orch()
    res = orch.run(  # missing question
        "query", {"question": "  "})
    assert not res.success
    assert res.state == WorkflowState.FAILED
    assert res.error_class == "terminal"


def test_answer_generated_event_emitted():
    orch = make_orch()
    seen = []
    orch.event_bus.subscribe(EventType.ANSWER_GENERATED, lambda e: seen.append(e))
    orch.run_query("hi")
    assert len(seen) == 1


# --------------------------------------------------------------------------- #
# Ingestion workflow
# --------------------------------------------------------------------------- #
def test_ingestion_workflow_success():
    deps = make_deps()
    orch = make_orch(deps)
    res = orch.run_ingestion("confluence://space")
    assert res.success
    assert res.output["documents_ingested"] == 2
    assert deps.indexing.indexed  # docs were written


def test_ingestion_emits_index_updated():
    orch = make_orch()
    events = []
    orch.event_bus.subscribe(EventType.INDEX_UPDATED, lambda e: events.append(e))
    orch.run_ingestion("src")
    assert events and events[0].payload["source"] == "src"


# --------------------------------------------------------------------------- #
# Reindex workflow
# --------------------------------------------------------------------------- #
def test_reindex_deletes_before_writing():
    deps = make_deps()
    orch = make_orch(deps)
    res = orch.run_reindex(["d1", "d2"], content=[{"id": "d1"}, {"id": "d2"}])
    assert res.success
    assert deps.indexing.deleted == ["d1", "d2"]
    assert deps.indexing.indexed


def test_reindex_requires_document_ids():
    orch = make_orch()
    res = orch.run("reindex", {})
    assert not res.success
    assert res.state == WorkflowState.FAILED


# --------------------------------------------------------------------------- #
# Retry logic
# --------------------------------------------------------------------------- #
class FlakyRetrieval:
    """Fails `fail_times` with a retryable error, then succeeds."""
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0
    def retrieve(self, q, top_k=10, filters=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientModuleError("temporary glitch")
        return [{"id": "d1"}]


def test_retry_eventually_succeeds():
    deps = make_deps(retrieval=FlakyRetrieval(fail_times=2))
    orch = make_orch(deps, retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0))
    res = orch.run_query("q")
    assert res.success
    assert res.retries == 2
    assert deps.retrieval.calls == 3


def test_retry_exhaustion_fails():
    deps = make_deps(retrieval=FlakyRetrieval(fail_times=10))
    orch = make_orch(deps, retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0))
    res = orch.run_query("q")
    assert not res.success
    assert res.state == WorkflowState.FAILED
    assert res.retries == 2  # attempts 2 and 3 are retries beyond first


def test_terminal_error_not_retried():
    class BadGen:
        def generate(self, question, context):
            raise ValueError("permanent")  # classified terminal
    deps = make_deps(generation=BadGen())
    orch = make_orch(deps, retry_policy=RetryPolicy(max_attempts=5, base_delay_s=0))
    res = orch.run_query("q")
    assert not res.success
    assert res.retries == 0  # no retries on terminal failure


def test_retry_metrics_and_events():
    deps = make_deps(retrieval=FlakyRetrieval(fail_times=1))
    orch = make_orch(deps, retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0))
    retried = []
    orch.event_bus.subscribe(EventType.JOB_RETRIED, lambda e: retried.append(e))
    orch.run_query("q")
    assert len(retried) == 1
    assert orch.metrics.snapshot()["retry_count"]["query"] == 1


# --------------------------------------------------------------------------- #
# State transitions
# --------------------------------------------------------------------------- #
def test_state_machine_legal_path():
    sm = StateMachine()
    assert sm.state == WorkflowState.PENDING
    sm.transition(WorkflowState.RUNNING)
    sm.transition(WorkflowState.COMPLETED)
    assert sm.is_terminal()


def test_state_machine_illegal_transition():
    sm = StateMachine()
    with pytest.raises(InvalidStateTransition):
        sm.transition(WorkflowState.COMPLETED)  # cannot skip RUNNING


def test_state_machine_no_transition_from_terminal():
    sm = StateMachine()
    sm.transition(WorkflowState.RUNNING)
    sm.transition(WorkflowState.FAILED)
    with pytest.raises(InvalidStateTransition):
        sm.transition(WorkflowState.RUNNING)


def test_successful_job_records_state_history():
    orch = make_orch()
    res = orch.run_query("q")
    hist = orch.status(res.job_id)["state_history"]
    assert hist == ["pending", "running", "completed"]


def test_retry_job_passes_through_retrying_state():
    deps = make_deps(retrieval=FlakyRetrieval(fail_times=1))
    orch = make_orch(deps, retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0))
    res = orch.run_query("q")
    hist = orch.status(res.job_id)["state_history"]
    assert "retrying" in hist
    assert hist[-1] == "completed"


# --------------------------------------------------------------------------- #
# Job management
# --------------------------------------------------------------------------- #
def test_job_status_and_history():
    orch = make_orch()
    res = orch.run_query("q")
    status = orch.status(res.job_id)
    assert status["state"] == "completed"
    assert status["workflow"] == "query"
    hist = orch.history()
    assert any(h["job_id"] == res.job_id for h in hist)


def test_job_cancellation_before_terminal():
    orch = make_orch()
    # Manually create a job in a non-terminal state to cancel.
    from backend.orchestration.workflow_context import WorkflowContext
    ctx = WorkflowContext(workflow_name="query")
    job = orch.jobs.create("query", ctx)
    assert orch.cancel(job.job_id) is True
    assert ctx.cancelled


def test_cancel_unknown_job_returns_false():
    orch = make_orch()
    assert orch.cancel("does-not-exist") is False


def test_cooperative_cancellation_stops_workflow():
    # A retrieval that cancels its own job mid-flight, then a slow step.
    class CancellingRetrieval:
        def __init__(self): self.ctx_ref = {}
        def retrieve(self, q, top_k=10, filters=None):
            return [{"id": "d1"}]
    orch = make_orch()
    # Cancel via event hook right after start, before generation runs.
    def cancel_on_start(e):
        orch.cancel(e.payload["job_id"])
    orch.event_bus.subscribe(EventType.ANSWER_GENERATED, cancel_on_start)
    # Cancellation is checked between steps; grounding/response steps remain.
    res = orch.run_query("q")
    assert res.state == WorkflowState.CANCELLED
    assert not res.success


# --------------------------------------------------------------------------- #
# Workflow failures
# --------------------------------------------------------------------------- #
def test_module_failure_marks_failed_with_trace():
    class BoomIndex(FakeIndexing):
        def index(self, docs): raise RuntimeError("disk full")
    deps = make_deps(indexing=BoomIndex())
    orch = make_orch(deps)
    res = orch.run_ingestion("src")
    assert not res.success
    assert res.state == WorkflowState.FAILED
    failed = [s for s in res.steps if s.state == "failed"]
    assert failed and failed[-1].name == "indexing"


def test_unknown_workflow_raises():
    orch = make_orch()
    with pytest.raises(Exception):
        orch.run("nonexistent", {})


def test_workflow_failed_event_emitted():
    class BoomGen:
        def generate(self, question, context): raise ValueError("nope")
    orch = make_orch(make_deps(generation=BoomGen()))
    failed = []
    orch.event_bus.subscribe(EventType.WORKFLOW_FAILED, lambda e: failed.append(e))
    orch.run_query("q")
    assert len(failed) == 1


def test_metrics_track_success_rate():
    orch = make_orch()
    orch.run_query("q")
    orch.run_query("q2")
    snap = orch.metrics_snapshot()
    assert snap["workflow_count"]["query"] == 2
    assert snap["job_success_rate"] == 1.0


def test_event_handler_failure_does_not_break_workflow():
    orch = make_orch()
    orch.event_bus.subscribe(EventType.WORKFLOW_STARTED,
                             lambda e: (_ for _ in ()).throw(RuntimeError("bad")))
    res = orch.run_query("q")
    assert res.success
    assert orch.event_bus.handler_errors >= 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
