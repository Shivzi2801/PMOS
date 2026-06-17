"""
Orchestrator.

The central execution engine of PMOS. It is the single public entry point the
API slice (and any operator tooling) calls to run a workflow. Responsibilities:

  - resolve a workflow by name from the registry
  - create and track a Job (state machine + context)
  - drive state transitions (PENDING -> RUNNING -> COMPLETED/FAILED/...)
  - run the workflow through the engine, wrapped in the RetryManager
  - publish lifecycle events on the event bus
  - record metrics
  - translate every outcome into a uniform WorkflowResult

It owns no business logic itself: every domain operation lives in a slice
(retrieval, indexing, generation, ...). The orchestrator only sequences,
retries, observes, and reports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import time
from typing import Any, Dict, Optional

from .errors import (
    JobCancelledError,
    OrchestrationError,
    RetryExhaustedError,
    classify,
)
from .event_bus import EventBus, EventType
from .job_manager import JobManager
from .metrics import Metrics
from .retry_manager import RetryManager, RetryPolicy
from .state_machine import WorkflowState
from .workflow_context import WorkflowContext
from .workflow_engine import WorkflowEngine
from .workflow_registry import WorkflowRegistry
from .workflow_result import WorkflowResult

from .query_workflow import build_query_workflow, WORKFLOW_NAME as QUERY
from .ingestion_workflow import build_ingestion_workflow, WORKFLOW_NAME as INGEST
from .reindex_workflow import build_reindex_workflow, WORKFLOW_NAME as REINDEX

logger = logging.getLogger("pmos.orchestration.orchestrator")


@dataclass
class Dependencies:
    """
    Wired collaborators from previous slices, injected once at startup.

    Any attribute may be None in tests; workflows only touch what they need.
    The duck-typed `_call` resolver in the workflow modules tolerates a range of
    method names so this layer is resilient to minor slice API drift.
    """
    connectors: Any = None
    ingestion: Any = None
    extraction: Any = None
    resolution: Any = None
    indexing: Any = None
    retrieval: Any = None
    context: Any = None
    generation: Any = None
    grounding: Any = None
    event_bus: Optional[EventBus] = None
    # timeouts (seconds)
    query_timeout_s: float = 60.0
    ingestion_timeout_s: float = 600.0
    reindex_timeout_s: float = 600.0


class Orchestrator:
    def __init__(self, deps: Dependencies, *,
                 retry_policy: Optional[RetryPolicy] = None,
                 registry: Optional[WorkflowRegistry] = None,
                 job_manager: Optional[JobManager] = None,
                 metrics: Optional[Metrics] = None,
                 event_bus: Optional[EventBus] = None,
                 sleep=None):
        self.event_bus = event_bus or deps.event_bus or EventBus()
        deps.event_bus = self.event_bus
        self.deps = deps
        self.metrics = metrics or Metrics()
        self.jobs = job_manager or JobManager()
        self.engine = WorkflowEngine()
        self.retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self.registry = registry or self._default_registry()

    # --- registry -----------------------------------------------------------
    def _default_registry(self) -> WorkflowRegistry:
        reg = WorkflowRegistry()
        reg.register(QUERY, build_query_workflow)
        reg.register(INGEST, build_ingestion_workflow)
        reg.register(REINDEX, build_reindex_workflow)
        return reg

    # --- public API ---------------------------------------------------------
    def run(self, workflow_name: str, payload: Dict[str, Any], *,
            tenant_id: Optional[str] = None,
            retry_policy: Optional[RetryPolicy] = None) -> WorkflowResult:
        """Synchronously run a workflow to a terminal state and return its result."""
        workflow = self.registry.build(workflow_name, self.deps)
        context = WorkflowContext(
            workflow_name=workflow_name, payload=payload, tenant_id=tenant_id,
        ).with_deadline(workflow.default_timeout_s)
        job = self.jobs.create(workflow_name, context)

        result = WorkflowResult(
            workflow_name=workflow_name, job_id=job.job_id,
            state=WorkflowState.PENDING, success=False,
        )
        self.metrics.record_start(workflow_name)
        self.event_bus.publish(EventType.WORKFLOW_STARTED,
                               job_id=job.job_id, workflow=workflow_name)

        policy = retry_policy or self.retry_policy
        retry = RetryManager(
            policy, sleep=self._sleep,
            on_retry=lambda attempt, err, delay: self._on_retry(
                job.job_id, workflow_name, attempt, err, delay, result),
        )
        started = time()

        def attempt() -> None:
            # Each attempt re-arms RUNNING from PENDING or RETRYING.
            self.jobs.transition(job.job_id, WorkflowState.RUNNING)
            self.engine.run(workflow, context, trace_sink=result.add_step)

        try:
            retry.run(attempt, is_cancelled=lambda: context.cancelled)
            result.output = context.get("response", context.data)
            result.success = True
            result.partial = any(
                k.startswith("_partial_failure::") for k in context.data
            )
            self.jobs.transition(job.job_id, WorkflowState.COMPLETED)
            result.state = WorkflowState.COMPLETED
            self.metrics.record_completion(workflow_name, time() - started)
            self.event_bus.publish(EventType.WORKFLOW_COMPLETED,
                                   job_id=job.job_id, workflow=workflow_name)
        except JobCancelledError as exc:
            self._finalize_failure(job.job_id, workflow_name, result, exc,
                                   started, WorkflowState.CANCELLED)
        except (RetryExhaustedError, OrchestrationError) as exc:
            self._finalize_failure(job.job_id, workflow_name, result, exc,
                                   started, WorkflowState.FAILED)
        except BaseException as exc:  # noqa: BLE001 - unexpected internal fault
            self.metrics.record_orchestration_error()
            self._finalize_failure(job.job_id, workflow_name, result, exc,
                                   started, WorkflowState.FAILED)

        result.retries = self.jobs.get(job.job_id).retries
        self.jobs.attach_result(job.job_id, result)
        return result

    # --- lifecycle helpers --------------------------------------------------
    def _on_retry(self, job_id, workflow_name, attempt, err, delay, result):
        result.retries += 1
        self.metrics.record_retry(workflow_name)
        self.jobs.mark_retry(job_id)
        # Move RUNNING -> RETRYING so observers see the intermediate state.
        job = self.jobs.get(job_id)
        if job and job.state == WorkflowState.RUNNING:
            self.jobs.transition(job_id, WorkflowState.RETRYING)
        self.event_bus.publish(
            EventType.JOB_RETRIED, job_id=job_id, workflow=workflow_name,
            attempt=attempt, delay_s=delay,
            error=str(err) if err else None,
        )

    def _finalize_failure(self, job_id, workflow_name, result, exc, started,
                          target_state):
        latency = time() - started
        result.success = False
        result.error = str(exc)
        result.error_class = classify(exc).value
        result.state = target_state
        # Guard against illegal transition if already terminal.
        job = self.jobs.get(job_id)
        if job and not job.sm.is_terminal():
            try:
                self.jobs.transition(job_id, target_state)
            except Exception:  # noqa: BLE001
                self.metrics.record_orchestration_error()
        if target_state == WorkflowState.FAILED:
            self.metrics.record_failure(workflow_name, latency)
            self.event_bus.publish(EventType.WORKFLOW_FAILED,
                                   job_id=job_id, workflow=workflow_name,
                                   error=str(exc))
        result.latency_s = round(latency, 6)
        logger.warning("workflow %s job %s -> %s: %s",
                       workflow_name, job_id, target_state.value, exc)

    # --- convenience wrappers ----------------------------------------------
    def run_query(self, question: str, **kw) -> WorkflowResult:
        return self.run(QUERY, {"question": question, **kw})

    def run_ingestion(self, source, **kw) -> WorkflowResult:
        return self.run(INGEST, {"source": source, **kw})

    def run_reindex(self, document_ids, **kw) -> WorkflowResult:
        return self.run(REINDEX, {"document_ids": document_ids, **kw})

    # --- job operations -----------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        return self.jobs.cancel(job_id)

    def status(self, job_id: str):
        return self.jobs.status(job_id)

    def history(self, limit: int = 50):
        return self.jobs.history(limit)

    def metrics_snapshot(self):
        return self.metrics.snapshot()
