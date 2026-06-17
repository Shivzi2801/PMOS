"""
Workflow state machine.

Defines the canonical lifecycle states for every workflow and the legal
transitions between them. The orchestrator drives transitions; illegal moves
raise InvalidStateTransition so bugs surface loudly instead of silently
corrupting job history.

Lifecycle:

    PENDING ──▶ RUNNING ──▶ COMPLETED
                  │  ▲           
                  │  └────── RETRYING ◀─┐
                  │                     │
                  └──▶ FAILED ──────────┘ (re-armed on retry)
                  │
                  └──▶ CANCELLED
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Set

from .errors import InvalidStateTransition


class WorkflowState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


# Allowed transitions. Anything not listed is rejected.
_TRANSITIONS: Dict[WorkflowState, Set[WorkflowState]] = {
    WorkflowState.PENDING: {WorkflowState.RUNNING, WorkflowState.CANCELLED},
    WorkflowState.RUNNING: {
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
        WorkflowState.RETRYING,
        WorkflowState.CANCELLED,
    },
    WorkflowState.RETRYING: {WorkflowState.RUNNING, WorkflowState.CANCELLED,
                             WorkflowState.FAILED},
    # Terminal states allow no further transitions.
    WorkflowState.COMPLETED: set(),
    WorkflowState.FAILED: set(),
    WorkflowState.CANCELLED: set(),
}

TERMINAL_STATES = {WorkflowState.COMPLETED, WorkflowState.FAILED,
                   WorkflowState.CANCELLED}


class StateMachine:
    """Tracks the state of a single workflow run and enforces legal transitions."""

    def __init__(self, initial: WorkflowState = WorkflowState.PENDING):
        self._state = initial
        self._history = [initial]

    @property
    def state(self) -> WorkflowState:
        return self._state

    @property
    def history(self):
        return list(self._history)

    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    def can_transition(self, target: WorkflowState) -> bool:
        return target in _TRANSITIONS.get(self._state, set())

    def transition(self, target: WorkflowState) -> WorkflowState:
        if not self.can_transition(target):
            raise InvalidStateTransition(
                f"cannot move from {self._state.value} to {target.value}"
            )
        self._state = target
        self._history.append(target)
        return self._state
