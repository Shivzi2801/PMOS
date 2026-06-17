"""
Internal event bus.

A lightweight, synchronous publish/subscribe bus used to decouple the
orchestrator from observers (metrics, audit logging, the API slice's
websocket/SSE notifiers, external webhooks). Handlers are invoked in
registration order. A failing handler never breaks the workflow: exceptions are
swallowed and counted so observability never becomes a source of outages.

This is deliberately in-process and dependency-free. Swapping in Kafka/NATS
later only requires reimplementing publish()/subscribe() with the same
signatures.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any, Callable, Dict, List

logger = logging.getLogger("pmos.orchestration.events")


class EventType(str, Enum):
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    JOB_RETRIED = "job_retried"
    INDEX_UPDATED = "index_updated"
    ANSWER_GENERATED = "answer_generated"


@dataclass
class Event:
    type: EventType
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self):
        self._subs: Dict[EventType, List[Handler]] = {}
        self._wildcard: List[Handler] = []
        self._lock = threading.RLock()
        self.handler_errors = 0

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        with self._lock:
            self._subs.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        """Receive every event regardless of type (useful for audit logging)."""
        with self._lock:
            self._wildcard.append(handler)

    def publish(self, event_type: EventType, **payload: Any) -> Event:
        event = Event(type=event_type, payload=payload)
        with self._lock:
            handlers = list(self._subs.get(event_type, [])) + list(self._wildcard)
        for h in handlers:
            try:
                h(event)
            except Exception:  # noqa: BLE001 - observers must never break workflows
                self.handler_errors += 1
                logger.exception("event handler failed for %s", event_type.value)
        return event
