"""
Tenant context.

The TenantContext is the authenticated, request-scoped "who and where" that
travels with every operation: which tenant, which workspace, which user (or API
key), and the roles in effect. Downstream slices read it to partition data and
to authorize actions.

It is stored in a `contextvars.ContextVar` so it propagates implicitly through a
call stack (and across async tasks) without every function needing it as an
explicit parameter — while still being isolated per request/coroutine. Helpers
`bind()` / `current()` / `clear()` plus a context-manager `use()` make the
lifecycle explicit and leak-free.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Optional

from .errors import MissingTenantContextError

_CTX: "contextvars.ContextVar[Optional[TenantContext]]" = contextvars.ContextVar(
    "pmos_tenant_context", default=None
)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    workspace_id: Optional[str] = None
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    session_id: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    # how the principal authenticated: "session" | "api_key" | "system"
    auth_method: str = "session"
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "api_key_id": self.api_key_id,
            "session_id": self.session_id,
            "roles": list(self.roles),
            "auth_method": self.auth_method,
            "correlation_id": self.correlation_id,
        }


def bind(ctx: TenantContext) -> contextvars.Token:
    """Bind a context to the current execution scope. Returns a reset token."""
    return _CTX.set(ctx)


def current(required: bool = True) -> Optional[TenantContext]:
    ctx = _CTX.get()
    if ctx is None and required:
        raise MissingTenantContextError("no tenant context bound to this request")
    return ctx


def clear(token: Optional[contextvars.Token] = None) -> None:
    if token is not None:
        _CTX.reset(token)
    else:
        _CTX.set(None)


@contextmanager
def use(ctx: TenantContext):
    """`with use(ctx): ...` — binds for the block and always cleans up."""
    token = bind(ctx)
    try:
        yield ctx
    finally:
        clear(token)
