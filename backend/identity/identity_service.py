"""
Identity service.

The public facade of the identity slice. It owns the in-memory stores for
tenants, workspaces, and users, wires together the RBAC engine, tenant resolver,
tenant guard, API-key manager, session manager, audit sink, and metrics, and
exposes a clean API for:

  - provisioning (create tenant / workspace / user)
  - authentication (login -> session) and logout
  - request resolution (credential -> TenantContext)
  - authorization checks (permission / isolation) via the guard
  - running code inside a tenant context (orchestration integration)

Everything other slices need is reachable from here. Stores are simple
dict-backed repositories implementing a `.get(id)` contract so they can be
swapped for a database without changing callers.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional

from .access_audit import AccessAudit
from .api_key_manager import APIKeyManager
from .auth_provider import AuthMethod, AuthProviderRegistry
from .errors import (
    AuthenticationError,
    DuplicateEntityError,
    InvalidUserError,
    UnknownTenantError,
    WorkspaceNotFoundError,
)
from .metrics import IdentityMetrics
from .rbac_engine import RBACEngine
from .session_manager import SessionManager
from .tenant import Tenant, TenantStatus
from .tenant_context import TenantContext, use as use_context
from .tenant_guard import TenantGuard
from .tenant_resolver import TenantResolver
from .user import User, UserStatus
from .workspace import Workspace, WorkspaceStatus


class _Store:
    """Minimal thread-safe dict repository with a `.get(id)` contract."""

    def __init__(self):
        self._items: Dict[str, object] = {}
        self._lock = threading.RLock()

    def put(self, key: str, value) -> None:
        with self._lock:
            self._items[key] = value

    def get(self, key: str):
        with self._lock:
            return self._items.get(key)

    def values(self):
        with self._lock:
            return list(self._items.values())

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._items.pop(key, None) is not None


class IdentityService:
    def __init__(self, *, session_ttl_seconds: float = 3600.0):
        self.tenants = _Store()
        self.workspaces = _Store()
        self.users = _Store()

        self.audit = AccessAudit()
        self.rbac = RBACEngine()
        self.api_keys = APIKeyManager(audit=self.audit)
        self.sessions = SessionManager(ttl_seconds=session_ttl_seconds,
                                       audit=self.audit)
        self.auth_providers = AuthProviderRegistry()

        self.metrics = IdentityMetrics(
            active_users_fn=self.sessions.active_count,
            active_tenants_fn=lambda: sum(
                1 for t in self.tenants.values()
                if t.status == TenantStatus.ACTIVE),
            active_workspaces_fn=lambda: sum(
                1 for w in self.workspaces.values()
                if w.status == WorkspaceStatus.ACTIVE),
        )
        # late-bind metrics into managers that emit them
        self.api_keys._metrics = self.metrics
        self.sessions._metrics = self.metrics

        self.resolver = TenantResolver(
            tenant_store=self.tenants, workspace_store=self.workspaces,
            api_key_manager=self.api_keys, session_manager=self.sessions,
            metrics=self.metrics, audit=self.audit,
        )
        self.guard = TenantGuard(self.rbac, audit=self.audit, metrics=self.metrics)

    # ------------------------------------------------------------------ #
    # Provisioning
    # ------------------------------------------------------------------ #
    def create_tenant(self, tenant_name: str, **kw) -> Tenant:
        tenant = Tenant(tenant_name=tenant_name, **kw)
        self.tenants.put(tenant.tenant_id, tenant)
        self.audit.record(action="tenant_created", tenant_id=tenant.tenant_id,
                          allowed=True, detail={"name": tenant_name})
        return tenant

    def create_workspace(self, tenant_id: str, workspace_name: str,
                         **kw) -> Workspace:
        if self.tenants.get(tenant_id) is None:
            raise UnknownTenantError("unknown tenant",
                                     detail={"tenant_id": tenant_id})
        ws = Workspace(tenant_id=tenant_id, workspace_name=workspace_name, **kw)
        self.workspaces.put(ws.workspace_id, ws)
        self.audit.record(action="workspace_created", tenant_id=tenant_id,
                          workspace_id=ws.workspace_id, allowed=True)
        return ws

    def create_user(self, tenant_id: str, email: str, *,
                    workspace_id: Optional[str] = None,
                    display_name: str = "",
                    roles: Optional[List[str]] = None) -> User:
        if self.tenants.get(tenant_id) is None:
            raise UnknownTenantError("unknown tenant",
                                     detail={"tenant_id": tenant_id})
        if workspace_id is not None:
            ws = self.workspaces.get(workspace_id)
            if ws is None or ws.tenant_id != tenant_id:
                raise WorkspaceNotFoundError(
                    "workspace not found in tenant",
                    detail={"tenant_id": tenant_id, "workspace_id": workspace_id})
        # enforce unique email within a tenant
        for u in self.users.values():
            if u.tenant_id == tenant_id and u.email.lower() == email.lower():
                raise DuplicateEntityError("user email already exists in tenant",
                                           detail={"email": email})
        user = User(tenant_id=tenant_id, email=email,
                    workspace_id=workspace_id, display_name=display_name)
        if roles:
            user.roles = list(roles)
        self.users.put(user.user_id, user)
        self.audit.record(action="user_created", tenant_id=tenant_id,
                          workspace_id=workspace_id, user_id=user.user_id,
                          allowed=True)
        return user

    # ------------------------------------------------------------------ #
    # Authentication / sessions
    # ------------------------------------------------------------------ #
    def login(self, user_id: str) -> "Session":  # noqa: F821
        """
        Create a session for an already-authenticated user. Real credential
        verification is delegated to an AuthProvider; in this slice the caller
        proves identity out-of-band and we issue the session.
        """
        user = self.users.get(user_id)
        if user is None:
            self.metrics.incr_auth_attempt("user_not_found")
            self.audit.record(action="auth_failed", allowed=False,
                             detail={"user_id": user_id})
            raise InvalidUserError("unknown user", detail={"user_id": user_id})
        if not user.is_active:
            self.metrics.incr_auth_attempt("user_inactive")
            self.audit.record(action="auth_failed", tenant_id=user.tenant_id,
                             user_id=user_id, allowed=False,
                             detail={"status": user.status.value})
            raise AuthenticationError("user is not active")
        session = self.sessions.create(
            tenant_id=user.tenant_id, user_id=user.user_id,
            roles=list(user.roles), workspace_id=user.workspace_id,
        )
        self.metrics.incr_auth_attempt("success")
        self.audit.record(action="auth_success", tenant_id=user.tenant_id,
                         user_id=user_id, allowed=True,
                         detail={"session_id": session.session_id})
        return session

    def logout(self, session_id: str) -> bool:
        return self.sessions.revoke(session_id)

    def authenticate_with_provider(self, method: AuthMethod,
                                   credential: dict) -> "Session":  # noqa: F821
        """Authenticate via a registered provider, then issue a session."""
        provider = self.auth_providers.get(method)
        if provider is None:
            raise AuthenticationError(f"no provider registered for {method.value}")
        principal = provider.authenticate(credential)  # may raise NotImplementedError for stubs
        return self.sessions.create(
            tenant_id=principal.tenant_id, user_id=principal.user_id,
            roles=list(principal.roles), workspace_id=principal.workspace_id,
        )

    # ------------------------------------------------------------------ #
    # Request resolution
    # ------------------------------------------------------------------ #
    def resolve(self, *, api_key: Optional[str] = None,
                session_token: Optional[str] = None,
                correlation_id: Optional[str] = None) -> TenantContext:
        if api_key:
            return self.resolver.resolve_from_api_key(api_key, correlation_id)
        if session_token:
            return self.resolver.resolve_from_session(session_token, correlation_id)
        raise AuthenticationError("no credential presented")

    def resolve_system(self, tenant_id: str,
                       workspace_id: Optional[str] = None,
                       correlation_id: Optional[str] = None) -> TenantContext:
        return self.resolver.resolve_system(tenant_id, workspace_id, correlation_id)

    # ------------------------------------------------------------------ #
    # Orchestration integration
    # ------------------------------------------------------------------ #
    @contextmanager
    def tenant_scope(self, ctx: TenantContext):
        """
        Bind a TenantContext for a block of work (e.g. an orchestration workflow
        run) so every nested operation is tenant-aware and auditable.
        """
        self.audit.record(action="tenant_scope_enter", ctx=ctx, allowed=True)
        with use_context(ctx):
            yield ctx

    def run_in_context(self, ctx: TenantContext, fn: Callable, *args, **kwargs):
        """Run `fn` with the given tenant context bound."""
        with self.tenant_scope(ctx):
            return fn(*args, **kwargs)

    # ------------------------------------------------------------------ #
    # Convenience authz pass-throughs
    # ------------------------------------------------------------------ #
    def require_permission(self, permission: str,
                           ctx: Optional[TenantContext] = None) -> None:
        self.guard.require_permission(permission, ctx)

    def has_permission(self, permission: str,
                       ctx: Optional[TenantContext] = None) -> bool:
        return self.guard.has_permission(permission, ctx)

    def metrics_snapshot(self) -> dict:
        return self.metrics.snapshot()
