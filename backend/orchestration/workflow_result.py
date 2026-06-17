"""
Workflow result.

The standard, serialisable outcome of any workflow run. Returned by the
orchestrator and persisted in job history. Captures success/failure, the
produced output, per-step traces, timing, and the final state. Designed so the
API slice can return it almost verbatim as a JSON response.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .state_machine import WorkflowState


@dataclass
class StepTrace:
    name: str
    state: str                       # "completed" | "failed" | "skipped"
    latency_s: float = 0.0
    attempts: int = 1
    error: Optional[str] = None


@dataclass
class WorkflowResult:
    workflow_name: str
    job_id: str
    state: WorkflowState
    success: bool
    output: Dict[str, Any] = field(default_factory=dict)
    steps: List[StepTrace] = field(default_factory=list)
    error: Optional[str] = None
    error_class: Optional[str] = None
    latency_s: float = 0.0
    retries: int = 0
    partial: bool = False

    def add_step(self, trace: StepTrace) -> None:
        self.steps.append(trace)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d
