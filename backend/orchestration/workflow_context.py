"""
Workflow context.

A mutable carrier object threaded through every step of a workflow. It holds:

  - the immutable input payload
  - a scratch `data` dict where steps publish their outputs for later steps
  - correlation identifiers (job_id, correlation_id, tenant_id)
  - a deadline for timeout enforcement
  - a cooperative cancellation flag

Steps read what they need from `context.data` and write their results back,
so the workflow definition stays declarative (an ordered list of steps) while
data flows implicitly through the shared context.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from time import time
from typing import Any, Dict, Optional


@dataclass
class WorkflowContext:
    workflow_name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time)
    deadline_at: Optional[float] = None
    _cancelled: bool = False

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self.data:
            raise KeyError(f"workflow context missing required key: {key}")
        return self.data[key]

    # --- cancellation -------------------------------------------------------
    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # --- timeouts -----------------------------------------------------------
    def with_deadline(self, timeout_s: Optional[float]) -> "WorkflowContext":
        if timeout_s:
            self.deadline_at = time() + timeout_s
        return self

    def time_remaining(self) -> Optional[float]:
        if self.deadline_at is None:
            return None
        return self.deadline_at - time()

    def is_expired(self) -> bool:
        rem = self.time_remaining()
        return rem is not None and rem <= 0
