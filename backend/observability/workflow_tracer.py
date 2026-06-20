"""
PMOS Observability & Monitoring — Workflow Tracer (S2.6)

A thin, opinionated layer over :class:`DistributedTracer` specialized for the
Workflow Orchestration slice (S2.2). It models the canonical PMOS request
pipeline as a span tree:

    workflow:<name>
      ├─ step:ingestion
      ├─ step:retrieval
      ├─ step:context_assembly
      ├─ step:generation
      └─ step:grounding

While doing so it also records the workflow/step duration metrics defined in
the registry, so a single ``workflow_span``/``step`` call produces both a trace
span *and* the corresponding timing histogram — keeping instrumentation call
sites terse and consistent across the platform.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Callable, Iterator, Mapping, Optional

from .distributed_tracer import DistributedTracer
from .trace_context import TraceContext
from .trace_span import SpanKind, SpanStatus, TraceSpan


# Recorder hooks injected by the service so this module stays decoupled from
# the concrete MetricsCollector.
WorkflowMetricRecorder = Callable[..., None]


class WorkflowTracer:
    """Specialized tracer for workflow + step instrumentation.

    Parameters
    ----------
    tracer:
        The underlying :class:`DistributedTracer`.
    record_workflow:
        Callback ``(workflow, status, tenant, duration_ms) -> None`` to record
        workflow-level duration + run count.
    record_step:
        Callback ``(workflow, step, status, tenant, duration_ms) -> None`` to
        record step-level duration.
    clock:
        Injectable epoch-seconds clock.
    """

    def __init__(
        self,
        tracer: DistributedTracer,
        *,
        record_workflow: Optional[WorkflowMetricRecorder] = None,
        record_step: Optional[WorkflowMetricRecorder] = None,
        clock=time.time,
    ) -> None:
        self._tracer = tracer
        self._record_workflow = record_workflow
        self._record_step = record_step
        self._clock = clock

    @contextlib.contextmanager
    def workflow_span(
        self,
        workflow: str,
        *,
        tenant_id: Optional[str] = None,
        parent: Optional[TraceContext] = None,
        attributes: Optional[Mapping[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> Iterator[TraceSpan]:
        """Open the root span for a workflow execution."""
        attrs = {"workflow.name": workflow}
        if workflow_id:
            attrs["workflow.id"] = workflow_id
        if attributes:
            attrs.update(attributes)

        start = self._clock()
        status = "success"
        with self._tracer.span(
            f"workflow:{workflow}",
            kind=SpanKind.INTERNAL,
            parent=parent,
            tenant_id=tenant_id,
            attributes=attrs,
        ) as span:
            try:
                yield span
            except BaseException:
                status = "error"
                raise
            finally:
                duration_ms = (self._clock() - start) * 1000.0
                if span._status is SpanStatus.ERROR:  # noqa: SLF001 - same package intent
                    status = "error"
                if self._record_workflow is not None:
                    with contextlib.suppress(Exception):
                        self._record_workflow(
                            workflow=workflow,
                            status=status,
                            tenant=tenant_id or "unknown",
                            duration_ms=duration_ms,
                        )

    @contextlib.contextmanager
    def step_span(
        self,
        workflow: str,
        step: str,
        *,
        tenant_id: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[TraceSpan]:
        """Open a child span for one workflow step.

        Inherits the active workflow context automatically via the tracer's
        context var, so callers need not thread the parent manually.
        """
        attrs = {"workflow.name": workflow, "workflow.step": step}
        if attributes:
            attrs.update(attributes)

        start = self._clock()
        status = "success"
        with self._tracer.span(
            f"step:{step}",
            kind=SpanKind.INTERNAL,
            tenant_id=tenant_id,
            attributes=attrs,
        ) as span:
            try:
                yield span
            except BaseException:
                status = "error"
                raise
            finally:
                duration_ms = (self._clock() - start) * 1000.0
                if span._status is SpanStatus.ERROR:  # noqa: SLF001
                    status = "error"
                if self._record_step is not None:
                    with contextlib.suppress(Exception):
                        self._record_step(
                            workflow=workflow,
                            step=step,
                            status=status,
                            tenant=tenant_id or "unknown",
                            duration_ms=duration_ms,
                        )


__all__ = ["WorkflowTracer", "WorkflowMetricRecorder"]
