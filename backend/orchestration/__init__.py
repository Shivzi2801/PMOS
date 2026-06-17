"""
PMOS Orchestration layer (Slice S2.2).

The central execution engine that coordinates every PMOS workflow. It wires the
previously-built slices (connectors, ingestion, extraction, resolution,
indexing, retrieval, context, generation, grounding) into ordered workflows and
manages their lifecycle: state, retries, events, jobs, metrics, and failure
recovery.

Public surface:

    from backend.orchestration import Orchestrator, Dependencies, RetryPolicy

    orch = Orchestrator(Dependencies(retrieval=..., generation=..., ...))
    result = orch.run_query("What changed in the Q3 roadmap?")
"""

from .orchestrator import Orchestrator, Dependencies
from .retry_manager import RetryManager, RetryPolicy
from .state_machine import WorkflowState, StateMachine
from .event_bus import EventBus, EventType, Event
from .job_manager import JobManager, Job
from .metrics import Metrics
from .workflow_context import WorkflowContext
from .workflow_result import WorkflowResult, StepTrace
from .workflow_engine import Workflow, Step, WorkflowEngine
from .workflow_registry import WorkflowRegistry
from . import errors

__all__ = [
    "Orchestrator",
    "Dependencies",
    "RetryManager",
    "RetryPolicy",
    "WorkflowState",
    "StateMachine",
    "EventBus",
    "EventType",
    "Event",
    "JobManager",
    "Job",
    "Metrics",
    "WorkflowContext",
    "WorkflowResult",
    "StepTrace",
    "Workflow",
    "Step",
    "WorkflowEngine",
    "WorkflowRegistry",
    "errors",
]

__version__ = "2.2.0"
