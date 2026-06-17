"""
Workflow registry.

A name -> Workflow factory mapping. The orchestrator looks up workflows here at
dispatch time. Factories receive the wired module dependencies (retrieval,
indexing, generation, ...) so workflows are constructed with their real
collaborators injected, keeping the workflow definitions themselves free of
import-time coupling.

Registering by factory (rather than by instance) lets each run get a fresh,
correctly-parameterised Workflow and makes dependency injection in tests
trivial.
"""

from __future__ import annotations

from typing import Callable, Dict

from .errors import WorkflowNotFoundError
from .workflow_engine import Workflow

# A factory takes the orchestrator's dependency bundle and returns a Workflow.
WorkflowFactory = Callable[["Dependencies"], Workflow]  # noqa: F821


class WorkflowRegistry:
    def __init__(self):
        self._factories: Dict[str, WorkflowFactory] = {}

    def register(self, name: str, factory: WorkflowFactory) -> None:
        self._factories[name] = factory

    def has(self, name: str) -> bool:
        return name in self._factories

    def names(self):
        return sorted(self._factories.keys())

    def build(self, name: str, deps) -> Workflow:
        if name not in self._factories:
            raise WorkflowNotFoundError(f"no workflow registered as '{name}'")
        return self._factories[name](deps)
