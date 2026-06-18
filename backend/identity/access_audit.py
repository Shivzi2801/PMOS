"""
Access audit.

An append-only record of security-relevant events. In a SaaS platform the audit
trail is a first-class product feature (enterprise customers require it for
SOC 2 / ISO 27001 / GDPR accountability) and the primary forensic tool when
something goes wrong.

Tracked event families (per spec):
  - user actions               (action=<verb>)
  - permission checks          (action="permission_check", allowed=bool)
  - tenant access              (action="tenant_access")
  - api key usage              (action="api_key_used" / "api_key_*")
  - authentication events      (action="auth_*", "session_*")

Each record is immutable, timestamped, and tenant-tagged so audit data is itself
tenant-isolated. This in-memory sink implements a stable interface; a durable
implementation (append-only table, object storage, SIEM forwarder) can drop in
behind the same `record()` / `query()` methods.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from time import time
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AuditRecord:
    action: str
    allowed: bool
    ts: float = field(default_factory=time)
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: Optional[str] = None
    workspace_id: Optional[str] = None
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    session_id: Optional[str] = None
    auth_method: Optional[str] = None
    correlation_id: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "ts": self.ts,
            "action": self.action,
            "allowed": self.allowed,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "api_key_id": self.api_key_id,
            "session_id": self.session_id,
            "auth_method": self.auth_method,
            "correlation_id": self.correlation_id,
            "detail": dict(self.detail),
        }


class AccessAudit:
    def __init__(self, capacity: int = 100_000):
        self._records: List[AuditRecord] = []
        self._lock = threading.RLock()
        self._capacity = capacity

    def record(self, *, action: str, allowed: bool = True,
               ctx=None, tenant_id: Optional[str] = None,
               workspace_id: Optional[str] = None,
               user_id: Optional[str] = None,
               api_key_id: Optional[str] = None,
               session_id: Optional[str] = None,
               auth_method: Optional[str] = None,
               correlation_id: Optional[str] = None,
               detail: Optional[Dict[str, Any]] = None) -> AuditRecord:
        # If a TenantContext is supplied, pull identifiers from it.
        if ctx is not None:
            tenant_id = tenant_id or getattr(ctx, "tenant_id", None)
            workspace_id = workspace_id or getattr(ctx, "workspace_id", None)
            user_id = user_id or getattr(ctx, "user_id", None)
            api_key_id = api_key_id or getattr(ctx, "api_key_id", None)
            session_id = session_id or getattr(ctx, "session_id", None)
            auth_method = auth_method or getattr(ctx, "auth_method", None)
            correlation_id = correlation_id or getattr(ctx, "correlation_id", None)

        rec = AuditRecord(
            action=action, allowed=allowed, tenant_id=tenant_id,
            workspace_id=workspace_id, user_id=user_id, api_key_id=api_key_id,
            session_id=session_id, auth_method=auth_method,
            correlation_id=correlation_id, detail=detail or {},
        )
        with self._lock:
            self._records.append(rec)
            if len(self._records) > self._capacity:
                self._records.pop(0)
        return rec

    def query(self, *, tenant_id: Optional[str] = None,
              action: Optional[str] = None, allowed: Optional[bool] = None,
              limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = reversed(self._records)
            out = []
            for r in rows:
                if tenant_id is not None and r.tenant_id != tenant_id:
                    continue
                if action is not None and r.action != action:
                    continue
                if allowed is not None and r.allowed != allowed:
                    continue
                out.append(r.to_dict())
                if len(out) >= limit:
                    break
            return out

    def count(self) -> int:
        with self._lock:
            return len(self._records)
