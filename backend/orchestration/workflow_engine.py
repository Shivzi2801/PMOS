"""
Workflow engine.

Defines the `Step` and `Workflow` abstractions and the executor that runs a
workflow's steps in order against a shared WorkflowContext.

A Workflow is a declarative, ordered list of Steps. Each Step is a named
callable that reads/writes the context. The engine is responsible only for
*mechanics*: sequencing, per-step timing, timeout/cancellation checks, step
tracing, and translating step exceptions into the orchestration error taxonomy.
Retry and state management live one layer up (orchestrator + retry_manager) so
the engine stays a pure executor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import time
from typing import Callable, List, Optional

from .errors import (
    JobCancelledError,
    ModuleFailureError,
    StepTimeoutError,
    classify,
    FailureClass,
)
from .workflow_context import WorkflowContext
from .workflow_result import StepTrace

logger = logging.getLogger("pmos.orchestration.engine")

# A step receives the context and mutates it. Returning is optional.
StepFn = Callable[[WorkflowContext], None]


@dataclass
class Step:
    name: str
    fn: StepFn
    # If True, a failure here degrades rather than aborts the workflow
    # (used for partial-failure-tolerant steps such as optional enrichment).
    optional: bool = False


class Workflow:
    def __init__(self, name: str, steps: List[Step], *,
                 default_timeout_s: Optional[float] = None):
        self.name = name
        self.steps = steps
        self.default_timeout_s = default_timeout_s

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Workflow {self.name} steps={[s.name for s in self.steps]}>"


class WorkflowEngine:
    """Executes a Workflow's steps sequentially against a context."""

    def run(self, workflow: Workflow, context: WorkflowContext,
            trace_sink: Optional[Callable[[StepTrace], None]] = None) -> List[StepTrace]:
        traces: List[StepTrace] = []
        for step in workflow.steps:
            # Cooperative cancellation check between steps.
            if context.cancelled:
                raise JobCancelledError("job cancelled during execution",
                                        step=step.name)
            # Timeout check before starting an expensive step.
            if context.is_expired():
                raise StepTimeoutError("workflow deadline exceeded",
                                       step=step.name)

            started = time()
            try:
                step.fn(context)
                latency = time() - started
                trace = StepTrace(name=step.name, state="completed",
                                  latency_s=round(latency, 6))
            except JobCancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001
                latency = time() - started
                trace = StepTrace(
                    name=step.name,
                    state="failed",
                    latency_s=round(latency, 6),
                    error=str(exc),
                )
                if trace_sink:
                    trace_sink(trace)
                traces.append(trace)

                if step.optional:
                    # Degrade: record the failure and continue.
                    logger.warning("optional step %s failed, continuing: %s",
                                   step.name, exc)
                    context.set(f"_partial_failure::{step.name}", str(exc))
                    continue

                # Preserve classification: retryable stays retryable so the
                # orchestrator's RetryManager can act on it.
                if classify(exc) is FailureClass.RETRYABLE:
                    raise
                raise ModuleFailureError(
                    f"step '{step.name}' failed", cause=exc, step=step.name
                ) from exc

            if trace_sink:
                trace_sink(trace)
            traces.append(trace)
        return traces
