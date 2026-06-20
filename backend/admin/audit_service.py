"""Audit framework: tamper-evident, append-only event log.

The audit service is the platform's source of truth for "what happened". Every
state-changing admin operation funnels a record here. Events are chained by hash
(each references the prior event's hash), so any modification of historical
records is detectable via :meth:`verify_chain`.

The store is pluggable through :class:`AuditStore`; the default keeps events in
memory. Production deployments inject a durable, write-once backend (e.g. an
append-only table or object store with retention locks).
"""
from __future__ import annotations

import threading
from typing import Any, Mapping, Optional, Protocol

from .metrics import AUDIT_EVENTS, get_metrics
from .models import AuditCategory, AuditEvent


class AuditStore(Protocol):
    """Persistence contract for audit events."""

    def append(self, event: AuditEvent) -> None: ...
    def last(self) -> Optional[AuditEvent]: ...
    def all(self) -> list[AuditEvent]: ...
    def count(self) -> int: ...


class InMemoryAuditStore:
    """Thread-safe in-memory append-only store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def last(self) -> Optional[AuditEvent]:
        with self._lock:
            return self._events[-1] if self._events else None

    def all(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)

    def count(self) -> int:
        with self._lock:
            return len(self._events)


class AuditService:
    """Record and query audit events with chain-integrity guarantees."""

    def __init__(self, *, store: Optional[AuditStore] = None) -> None:
        self._store: AuditStore = store or InMemoryAuditStore()
        self._lock = threading.Lock()

    def record(
        self,
        *,
        category: AuditCategory,
        action: str,
        actor_id: str,
        target_type: str = "",
        target_id: str = "",
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        outcome: str = "success",
        message: str = "",
        actor_type: str = "user",
        before: Optional[Mapping[str, Any]] = None,
        after: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> AuditEvent:
        """Append a new audit event, sealing it into the hash chain."""
        with self._lock:
            previous = self._store.last()
            event = AuditEvent(
                category=category,
                action=action,
                actor_id=actor_id,
                actor_type=actor_type,
                target_type=target_type,
                target_id=target_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                outcome=outcome,
                message=message,
                before=dict(before) if before is not None else None,
                after=dict(after) if after is not None else None,
                metadata=dict(metadata or {}),
                sequence=(previous.sequence + 1) if previous else 1,
                previous_hash=previous.event_hash if previous else None,
            )
            event.event_hash = event.compute_hash()
            self._store.append(event)

        get_metrics().increment(
            AUDIT_EVENTS, category=category.value, outcome=outcome
        )
        return event

    # -- queries ----------------------------------------------------------- #
    def query(
        self,
        *,
        category: Optional[AuditCategory] = None,
        actor_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        target_id: Optional[str] = None,
        action: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[AuditEvent]:
        """Filter events by any combination of attributes (AND semantics)."""
        results = []
        for ev in self._store.all():
            if category is not None and ev.category != category:
                continue
            if actor_id is not None and ev.actor_id != actor_id:
                continue
            if tenant_id is not None and ev.tenant_id != tenant_id:
                continue
            if workspace_id is not None and ev.workspace_id != workspace_id:
                continue
            if target_id is not None and ev.target_id != target_id:
                continue
            if action is not None and ev.action != action:
                continue
            if outcome is not None and ev.outcome != outcome:
                continue
            results.append(ev)
        results.sort(key=lambda e: e.sequence, reverse=True)
        if limit is not None:
            results = results[:limit]
        return results

    def all_events(self) -> list[AuditEvent]:
        return self._store.all()

    def count(self) -> int:
        return self._store.count()

    def verify_chain(self) -> bool:
        """Verify integrity of the entire audit chain.

        Returns True iff every event's stored hash matches its recomputed hash
        and correctly references the previous event. Detects tampering, deletion
        and reordering.
        """
        previous_hash: Optional[str] = None
        expected_seq = 1
        for ev in self._store.all():
            if ev.sequence != expected_seq:
                return False
            if ev.previous_hash != previous_hash:
                return False
            if ev.event_hash != ev.compute_hash():
                return False
            previous_hash = ev.event_hash
            expected_seq += 1
        return True

    # Convenience wrappers (semantic helpers requested by the spec) -------- #
    def log_admin_action(self, **kwargs: Any) -> AuditEvent:
        return self.record(category=AuditCategory.ADMIN_ACTION, **kwargs)

    def log_security_event(self, **kwargs: Any) -> AuditEvent:
        return self.record(category=AuditCategory.SECURITY, **kwargs)
