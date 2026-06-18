"""
Tests for the PMOS identity / access-control / multi-tenancy slice.

Run with:  pytest backend/identity/test_identity.py -q

Covers: tenant resolution, workspace resolution, RBAC evaluation, permission
checks, API-key validation, session management, tenant isolation, and
authorization failures.
"""

from __future__ import annotations

import pytest

from backend.identity import (
    IdentityService, DefaultRoles, Permissions, RBACEngine, TenantContext,
    APIKeyManager, AuthMethod,
)
from backend.identity.errors import (
    UnknownTenantError, WorkspaceNotFoundError, InvalidUserError,
    SessionExpiredError, InvalidAPIKeyError, PermissionDeniedError,
    CrossTenantAccessError, DuplicateEntityError,
)


@pytest.fixture
def idp():
    return IdentityService()


@pytest.fixture
def acme(idp):
    t = idp.create_tenant("Acme Corp")
    ws = idp.create_workspace(t.tenant_id, "Product Team")
    return idp, t, ws


# --------------------------------------------------------------------------- #
# Tenant & workspace resolution
# --------------------------------------------------------------------------- #
def test_create_and_resolve_tenant(acme):
    idp, t, ws = acme
    assert idp.tenants.get(t.tenant_id).tenant_name == "Acme Corp"
    assert ws.tenant_id == t.tenant_id


def test_workspace_must_belong_to_tenant(idp):
    t1 = idp.create_tenant("T1")
    t2 = idp.create_tenant("T2")
    ws2 = idp.create_workspace(t2.tenant_id, "W2")
    # creating a user in t1 pointing at t2's workspace must fail
    with pytest.raises(WorkspaceNotFoundError):
        idp.create_user(t1.tenant_id, "x@t1.com", workspace_id=ws2.workspace_id)


def test_unknown_tenant_on_workspace_create(idp):
    with pytest.raises(UnknownTenantError):
        idp.create_workspace("tnt_does_not_exist", "Ghost")


def test_session_resolution_builds_context(acme):
    idp, t, ws = acme
    user = idp.create_user(t.tenant_id, "pm@acme.com",
                           workspace_id=ws.workspace_id, roles=["MANAGER"])
    session = idp.login(user.user_id)
    ctx = idp.resolve(session_token=session.token)
    assert ctx.tenant_id == t.tenant_id
    assert ctx.workspace_id == ws.workspace_id
    assert ctx.user_id == user.user_id
    assert ctx.auth_method == "session"


def test_resolution_rejects_unknown_tenant_after_deletion(acme):
    idp, t, ws = acme
    user = idp.create_user(t.tenant_id, "a@acme.com")
    session = idp.login(user.user_id)
    idp.tenants.delete(t.tenant_id)  # tenant vanishes
    with pytest.raises(UnknownTenantError):
        idp.resolve(session_token=session.token)


# --------------------------------------------------------------------------- #
# RBAC evaluation & permission checks
# --------------------------------------------------------------------------- #
def test_rbac_inheritance():
    rbac = RBACEngine()
    # MANAGER inherits ANALYST inherits VIEWER
    assert rbac.has_permission([DefaultRoles.MANAGER], Permissions.VIEW_DOCUMENTS)
    assert rbac.has_permission([DefaultRoles.MANAGER], Permissions.INGEST_DOCUMENTS)
    assert rbac.has_permission([DefaultRoles.MANAGER], Permissions.MANAGE_USERS)


def test_viewer_cannot_manage_users():
    rbac = RBACEngine()
    assert not rbac.has_permission([DefaultRoles.VIEWER], Permissions.MANAGE_USERS)


def test_admin_wildcard_grants_everything():
    rbac = RBACEngine()
    assert rbac.has_permission([DefaultRoles.ADMIN], Permissions.MANAGE_API_KEYS)
    assert rbac.has_permission([DefaultRoles.ADMIN], "some_future_permission")


def test_require_permission_allows_and_denies(acme):
    idp, t, ws = acme
    mgr = idp.create_user(t.tenant_id, "m@acme.com", roles=["MANAGER"])
    viewer = idp.create_user(t.tenant_id, "v@acme.com", roles=["VIEWER"])
    mgr_ctx = idp.resolve(session_token=idp.login(mgr.user_id).token)
    viewer_ctx = idp.resolve(session_token=idp.login(viewer.user_id).token)

    idp.require_permission(Permissions.MANAGE_USERS, mgr_ctx)  # no raise
    with pytest.raises(PermissionDeniedError):
        idp.require_permission(Permissions.MANAGE_USERS, viewer_ctx)


def test_permission_denial_increments_metric(acme):
    idp, t, ws = acme
    viewer = idp.create_user(t.tenant_id, "v@acme.com", roles=["VIEWER"])
    ctx = idp.resolve(session_token=idp.login(viewer.user_id).token)
    with pytest.raises(PermissionDeniedError):
        idp.require_permission(Permissions.ADMIN_ACCESS, ctx)
    assert idp.metrics.snapshot()["permission_denials"] == 1


# --------------------------------------------------------------------------- #
# API key validation
# --------------------------------------------------------------------------- #
def test_api_key_create_and_validate(acme):
    idp, t, ws = acme
    record, raw = idp.api_keys.create(t.tenant_id, "ci-pipeline",
                                      roles=["ANALYST"])
    validated = idp.api_keys.validate(raw)
    assert validated.key_id == record.key_id
    assert validated.tenant_id == t.tenant_id


def test_api_key_resolution_builds_context(acme):
    idp, t, ws = acme
    _, raw = idp.api_keys.create(t.tenant_id, "svc",
                                 roles=["ANALYST"], workspace_id=ws.workspace_id)
    ctx = idp.resolve(api_key=raw)
    assert ctx.tenant_id == t.tenant_id
    assert ctx.auth_method == "api_key"
    assert ctx.workspace_id == ws.workspace_id


def test_revoked_api_key_rejected(acme):
    idp, t, ws = acme
    record, raw = idp.api_keys.create(t.tenant_id, "svc")
    idp.api_keys.revoke(record.key_id)
    with pytest.raises(InvalidAPIKeyError):
        idp.api_keys.validate(raw)


def test_rotated_api_key_old_dead_new_works(acme):
    idp, t, ws = acme
    record, raw_old = idp.api_keys.create(t.tenant_id, "svc", roles=["VIEWER"])
    new_record, raw_new = idp.api_keys.rotate(record.key_id)
    with pytest.raises(InvalidAPIKeyError):
        idp.api_keys.validate(raw_old)
    assert idp.api_keys.validate(raw_new).key_id == new_record.key_id
    assert new_record.roles == ["VIEWER"]


def test_garbage_api_key_rejected(idp):
    with pytest.raises(InvalidAPIKeyError):
        idp.api_keys.validate("not-a-real-key")


def test_api_key_usage_metric(acme):
    idp, t, ws = acme
    _, raw = idp.api_keys.create(t.tenant_id, "svc")
    idp.api_keys.validate(raw)
    idp.api_keys.validate(raw)
    assert idp.metrics.snapshot()["api_key_usage_total"] == 2


# --------------------------------------------------------------------------- #
# Session management
# --------------------------------------------------------------------------- #
def test_session_expiry():
    from backend.identity.session_manager import SessionManager
    clock = {"t": 1000.0}
    sm = SessionManager(ttl_seconds=100, idle_timeout_seconds=None,
                        clock=lambda: clock["t"])
    s = sm.create(tenant_id="tnt", user_id="usr", roles=["VIEWER"])
    clock["t"] = 1050.0
    assert sm.validate(s.token).session_id == s.session_id  # still valid
    clock["t"] = 1101.0
    with pytest.raises(SessionExpiredError):
        sm.validate(s.token)


def test_session_idle_timeout():
    from backend.identity.session_manager import SessionManager
    clock = {"t": 0.0}
    sm = SessionManager(ttl_seconds=10_000, idle_timeout_seconds=100,
                        clock=lambda: clock["t"])
    s = sm.create(tenant_id="tnt", user_id="usr", roles=["VIEWER"])
    clock["t"] = 200.0
    with pytest.raises(SessionExpiredError):
        sm.validate(s.token)


def test_session_revocation(acme):
    idp, t, ws = acme
    user = idp.create_user(t.tenant_id, "u@acme.com")
    session = idp.login(user.user_id)
    assert idp.logout(session.session_id) is True
    with pytest.raises(Exception):
        idp.resolve(session_token=session.token)


def test_active_user_count(acme):
    idp, t, ws = acme
    u1 = idp.create_user(t.tenant_id, "u1@acme.com")
    u2 = idp.create_user(t.tenant_id, "u2@acme.com")
    idp.login(u1.user_id)
    idp.login(u2.user_id)
    assert idp.metrics.snapshot()["active_users"] == 2


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #
def test_cross_tenant_resource_access_blocked(acme):
    idp, t1, ws1 = acme
    t2 = idp.create_tenant("Globex")
    user = idp.create_user(t1.tenant_id, "u@acme.com", roles=["ADMIN"])
    ctx = idp.resolve(session_token=idp.login(user.user_id).token)
    # A resource owned by tenant 2:
    foreign_resource = {"tenant_id": t2.tenant_id, "doc": "secret"}
    with pytest.raises(CrossTenantAccessError):
        idp.guard.guard_resource(foreign_resource, ctx)


def test_same_tenant_resource_allowed(acme):
    idp, t, ws = acme
    user = idp.create_user(t.tenant_id, "u@acme.com", roles=["ADMIN"])
    ctx = idp.resolve(session_token=idp.login(user.user_id).token)
    own = {"tenant_id": t.tenant_id, "workspace_id": ws.workspace_id}
    idp.guard.guard_resource(own, ctx)  # no raise


def test_cross_workspace_blocked(acme):
    idp, t, ws = acme
    ws2 = idp.create_workspace(t.tenant_id, "Support Team")
    user = idp.create_user(t.tenant_id, "u@acme.com",
                           workspace_id=ws.workspace_id, roles=["ANALYST"])
    ctx = idp.resolve(session_token=idp.login(user.user_id).token)
    foreign_ws_resource = {"tenant_id": t.tenant_id,
                           "workspace_id": ws2.workspace_id}
    with pytest.raises(CrossTenantAccessError):
        idp.guard.guard_resource(foreign_ws_resource, ctx)


def test_api_key_cannot_cross_tenant(acme):
    idp, t1, ws1 = acme
    t2 = idp.create_tenant("Globex")
    _, raw = idp.api_keys.create(t1.tenant_id, "svc", roles=["ADMIN"])
    ctx = idp.resolve(api_key=raw)
    assert ctx.tenant_id == t1.tenant_id
    with pytest.raises(CrossTenantAccessError):
        idp.guard.require_same_tenant(t2.tenant_id, ctx)


# --------------------------------------------------------------------------- #
# Authorization failures & misc
# --------------------------------------------------------------------------- #
def test_login_unknown_user(idp):
    with pytest.raises(InvalidUserError):
        idp.login("usr_nope")


def test_duplicate_user_email(acme):
    idp, t, ws = acme
    idp.create_user(t.tenant_id, "dup@acme.com")
    with pytest.raises(DuplicateEntityError):
        idp.create_user(t.tenant_id, "dup@acme.com")


def test_resolve_without_credential_fails(idp):
    from backend.identity.errors import AuthenticationError
    with pytest.raises(AuthenticationError):
        idp.resolve()


# --------------------------------------------------------------------------- #
# Orchestration integration
# --------------------------------------------------------------------------- #
def test_run_in_tenant_context(acme):
    idp, t, ws = acme
    user = idp.create_user(t.tenant_id, "u@acme.com", roles=["ANALYST"])
    ctx = idp.resolve(session_token=idp.login(user.user_id).token)

    from backend.identity.tenant_context import current

    def workflow_body():
        bound = current()
        # permission check inside the workflow uses the bound context
        idp.require_permission(Permissions.RUN_INGESTION, bound)
        return bound.tenant_id

    result = idp.run_in_context(ctx, workflow_body)
    assert result == t.tenant_id


def test_system_context_has_full_access(acme):
    idp, t, ws = acme
    ctx = idp.resolve_system(t.tenant_id, workspace_id=ws.workspace_id)
    assert ctx.auth_method == "system"
    idp.require_permission(Permissions.RUN_REINDEX, ctx)  # SYSTEM wildcard


def test_auth_provider_stub_not_implemented(idp):
    from backend.identity.auth_provider import JWTAuthProvider
    idp.auth_providers.register(JWTAuthProvider())
    with pytest.raises(NotImplementedError):
        idp.authenticate_with_provider(AuthMethod.JWT, {"token": "x"})


def test_metrics_active_tenants_workspaces(acme):
    idp, t, ws = acme
    idp.create_tenant("Second")
    snap = idp.metrics.snapshot()
    assert snap["active_tenants"] == 2
    assert snap["active_workspaces"] == 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
