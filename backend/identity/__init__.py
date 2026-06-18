"""
PMOS Identity, Access Control & Multi-Tenancy layer (Slice S2.3).

The enterprise identity and tenancy foundation for PMOS as a SaaS platform.
Provides multi-org / multi-workspace / multi-user modelling, role-based access
control, API-key lifecycle management, pluggable authentication, session
management, tenant isolation enforcement, and a full access-audit trail.

Primary entry point:

    from backend.identity import IdentityService

    idp = IdentityService()
    acme = idp.create_tenant("Acme Corp")
    team = idp.create_workspace(acme.tenant_id, "Product Team")
    user = idp.create_user(acme.tenant_id, "pm@acme.com",
                           workspace_id=team.workspace_id, roles=["MANAGER"])
    session = idp.login(user.user_id)
    ctx = idp.resolve(session_token=session.token)
    idp.require_permission("ingest_documents", ctx)
"""

from .identity_service import IdentityService
from .tenant import Tenant, TenantStatus
from .workspace import Workspace, WorkspaceStatus
from .user import User, UserStatus
from .role import Role, DefaultRoles
from .permission import Permission, Permissions, WILDCARD
from .rbac_engine import RBACEngine
from .tenant_context import TenantContext, current, bind, clear, use
from .tenant_resolver import TenantResolver
from .tenant_guard import TenantGuard
from .api_key import APIKey, APIKeyStatus
from .api_key_manager import APIKeyManager
from .auth_provider import (
    AuthMethod, AuthProvider, AuthProviderRegistry, AuthenticatedPrincipal,
    JWTAuthProvider, OAuth2AuthProvider, SSOAuthProvider, SAMLAuthProvider,
    OIDCAuthProvider,
)
from .session_manager import Session, SessionManager
from .access_audit import AccessAudit, AuditRecord
from .metrics import IdentityMetrics
from . import errors

__all__ = [
    "IdentityService",
    "Tenant", "TenantStatus",
    "Workspace", "WorkspaceStatus",
    "User", "UserStatus",
    "Role", "DefaultRoles",
    "Permission", "Permissions", "WILDCARD",
    "RBACEngine",
    "TenantContext", "current", "bind", "clear", "use",
    "TenantResolver", "TenantGuard",
    "APIKey", "APIKeyStatus", "APIKeyManager",
    "AuthMethod", "AuthProvider", "AuthProviderRegistry", "AuthenticatedPrincipal",
    "JWTAuthProvider", "OAuth2AuthProvider", "SSOAuthProvider",
    "SAMLAuthProvider", "OIDCAuthProvider",
    "Session", "SessionManager",
    "AccessAudit", "AuditRecord",
    "IdentityMetrics",
    "errors",
]

__version__ = "2.3.0"
