"""
Job manager.

Owns the lifecycle bookkeeping for every workflow execution:

  - job creation        register a new job in PENDING
  - job tracking        attach state machine + live context handle
  - job status          query current state / result
  - job cancellation    flip the cooperative cancel flag on the context
  - job retries         expose retry counters; re-arm for RETRYING
  - workflow history    keep an append-only record of completed/failed jobs

This is an in-memory implementation backed by thread-safe dicts. The interface
is storage-agnostic: a Postgres/Redis-backed JobStore can implement the same
methods for durable, multi-process deployments.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from time import time
from typing import Dict, List, Optional

from .state_machine import StateMachine, WorkflowState
from .workflow_context import WorkflowContext
from .workflow_result import WorkflowResult


@dataclass
class Job:
    job_id: str
    workflow_name: str
    sm: StateMachine = field(default_factory=StateMachine)
    context: Optional[WorkflowContext] = None
    result: Optional[WorkflowResult] = None
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)
    retries: int = 0

    @property
    def state(self) -> WorkflowState:
        return self.sm.state

    def to_dict(self) -> Dict:
        return {
            "job_id": self.job_id,
            "workflow": self.workflow_name,
            "state": self.sm.state.value,
            "state_history": [s.value for s in self.sm.history],
            "retries": self.retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result.to_dict() if self.result else None,
        }


class JobManager:
    def __init__(self, history_limit: int = 1000):
        self._jobs: Dict[str, Job] = {}
        self._history: List[str] = []
        self._lock = threading.RLock()
        self._history_limit = history_limit

    # --- creation / tracking ------------------------------------------------
    def create(self, workflow_name: str, context: WorkflowContext) -> Job:
        with self._lock:
            job = Job(job_id=context.job_id, workflow_name=workflow_name,
                      context=context)
            self._jobs[job.job_id] = job
            return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def status(self, job_id: str) -> Optional[Dict]:
        job = self.get(job_id)
        return job.to_dict() if job else None

    # --- state transitions --------------------------------------------------
    def transition(self, job_id: str, target: WorkflowState) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.sm.transition(target)
            job.updated_at = time()

    def mark_retry(self, job_id: str) -> int:
        with self._lock:
            job = self._jobs[job_id]
            job.retries += 1
            job.updated_at = time()
            return job.retries

    def attach_result(self, job_id: str, result: WorkflowResult) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.result = result
            job.updated_at = time()
            self._record_history(job_id)

    # --- cancellation -------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.sm.is_terminal():
                return False
            if job.context:
                job.context.cancel()
            return True

    # --- history ------------------------------------------------------------
    def _record_history(self, job_id: str) -> None:
        self._history.append(job_id)
        if len(self._history) > self._history_limit:
            evicted = self._history.pop(0)
            # Keep terminal jobs reachable until evicted from history only.
            self._jobs.pop(evicted, None)

    def history(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            ids = self._history[-limit:][::-1]
            return [self._jobs[i].to_dict() for i in ids if i in self._jobs]

    def all_jobs(self) -> List[Dict]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]
