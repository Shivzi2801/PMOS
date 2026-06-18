"""
Session manager.

Issues and validates short-lived session tokens for interactive (human) users
after they authenticate through an auth provider. Sessions carry the tenant,
workspace, user, and roles so the tenant resolver can rebuild a TenantContext on
each request without re-hitting the identity provider.

Responsibilities: creation, expiration (idle + absolute TTL), validation,
revocation (single + all-for-user, e.g. on logout-everywhere or role change).
Tokens are opaque random strings; only their hash-free id is stored here (the
store is server-side, so the token itself need not be reversible).
"""

from __future__ import annotations

import secrets
import threading
import uuid
from dataclasses import dataclass, field
from time import time
from typing import Dict, List, Optional

from .errors import SessionExpiredError, SessionNotFoundError


def _new_session_id() -> str:
    return f"ses_{uuid.uuid4().hex[:16]}"


@dataclass
class Session:
    tenant_id: str
    user_id: str
    roles: List[str] = field(default_factory=list)
    workspace_id: Optional[str] = None
    session_id: str = field(default_factory=_new_session_id)
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created_at: float = field(default_factory=time)
    expires_at: float = 0.0
    last_seen_at: float = field(default_factory=time)
    revoked: bool = False

    def is_expired(self, now: Optional[float] = None) -> bool:
        now = now or time()
        return now >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "roles": list(self.roles),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
        }


class SessionManager:
    def __init__(self, *, ttl_seconds: float = 3600.0,
                 idle_timeout_seconds: Optional[float] = 900.0,
                 audit=None, metrics=None, clock=time):
        self._sessions: Dict[str, Session] = {}     # token -> Session
        self._by_id: Dict[str, str] = {}            # session_id -> token
        self._ttl = ttl_seconds
        self._idle = idle_timeout_seconds
        self._lock = threading.RLock()
        self._audit = audit
        self._metrics = metrics
        self._clock = clock

    # --- creation -----------------------------------------------------------
    def create(self, *, tenant_id: str, user_id: str, roles: List[str],
               workspace_id: Optional[str] = None) -> Session:
        now = self._clock()
        session = Session(
            tenant_id=tenant_id, user_id=user_id, roles=list(roles),
            workspace_id=workspace_id, created_at=now, last_seen_at=now,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._sessions[session.token] = session
            self._by_id[session.session_id] = session.token
        if self._audit:
            self._audit.record(action="session_created", tenant_id=tenant_id,
                               user_id=user_id, allowed=True,
                               detail={"session_id": session.session_id})
        return session

    # --- validation ---------------------------------------------------------
    def validate(self, token: str) -> Session:
        now = self._clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session.revoked:
                raise SessionNotFoundError("session not found")
            # Absolute expiry.
            if session.is_expired(now):
                self._purge(session)
                raise SessionExpiredError("session expired",
                                          detail={"session_id": session.session_id})
            # Idle timeout.
            if self._idle is not None and \
                    (now - session.last_seen_at) > self._idle:
                self._purge(session)
                raise SessionExpiredError("session idle-timed-out",
                                          detail={"session_id": session.session_id})
            session.last_seen_at = now
            return session

    # --- revocation ---------------------------------------------------------
    def revoke(self, session_id: str) -> bool:
        with self._lock:
            token = self._by_id.get(session_id)
            session = self._sessions.get(token) if token else None
            if session is None:
                return False
            session.revoked = True
            self._purge(session)
        if self._audit:
            self._audit.record(action="session_revoked", allowed=True,
                               detail={"session_id": session_id})
        return True

    def revoke_all_for_user(self, user_id: str) -> int:
        count = 0
        with self._lock:
            for session in list(self._sessions.values()):
                if session.user_id == user_id:
                    session.revoked = True
                    self._purge(session)
                    count += 1
        return count

    def active_count(self) -> int:
        now = self._clock()
        with self._lock:
            return sum(1 for s in self._sessions.values()
                       if not s.revoked and not s.is_expired(now))

    # --- helpers ------------------------------------------------------------
    def _purge(self, session: Session) -> None:
        self._sessions.pop(session.token, None)
        self._by_id.pop(session.session_id, None)
