"""
Authentication provider abstractions.

PMOS separates *authentication* (proving who you are) from *authorization*
(what you may do). Authorization is fully implemented here (RBAC). Authentication
is intentionally left as a set of production-ready *interfaces* so real providers
(JWT, OAuth2, SSO, SAML, OpenID Connect) can be plugged in later without
touching the rest of the slice.

Each provider takes an opaque credential and returns a normalised
`AuthenticatedPrincipal` (tenant, user, roles, external subject) that the
identity service turns into a session. Concrete providers are NOT implemented in
this slice — only the contract, a registry, and a development-only password
stub used by tests.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AuthMethod(str, Enum):
    PASSWORD = "password"   # dev/test only
    JWT = "jwt"
    OAUTH2 = "oauth2"
    SSO = "sso"
    SAML = "saml"
    OIDC = "openid_connect"


@dataclass
class AuthenticatedPrincipal:
    """Normalised result of a successful authentication, provider-agnostic."""
    tenant_id: str
    user_id: str
    roles: List[str] = field(default_factory=list)
    workspace_id: Optional[str] = None
    external_subject: Optional[str] = None
    method: AuthMethod = AuthMethod.PASSWORD
    claims: Dict[str, str] = field(default_factory=dict)


class AuthProvider(abc.ABC):
    """Contract every authentication backend must satisfy."""

    method: AuthMethod

    @abc.abstractmethod
    def authenticate(self, credential: dict) -> AuthenticatedPrincipal:
        """Verify a credential and return a principal, or raise AuthenticationError."""
        raise NotImplementedError


# --- Placeholder providers (interfaces only; no real verification) ----------

class JWTAuthProvider(AuthProvider):
    method = AuthMethod.JWT

    def __init__(self, *, issuer: str = "", audience: str = "",
                 jwks_url: str = ""):
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url

    def authenticate(self, credential: dict) -> AuthenticatedPrincipal:
        raise NotImplementedError("JWT provider not yet implemented (S2.3 stub)")


class OAuth2AuthProvider(AuthProvider):
    method = AuthMethod.OAUTH2

    def __init__(self, *, client_id: str = "", token_url: str = ""):
        self.client_id = client_id
        self.token_url = token_url

    def authenticate(self, credential: dict) -> AuthenticatedPrincipal:
        raise NotImplementedError("OAuth2 provider not yet implemented (S2.3 stub)")


class SSOAuthProvider(AuthProvider):
    method = AuthMethod.SSO

    def authenticate(self, credential: dict) -> AuthenticatedPrincipal:
        raise NotImplementedError("SSO provider not yet implemented (S2.3 stub)")


class SAMLAuthProvider(AuthProvider):
    method = AuthMethod.SAML

    def __init__(self, *, idp_metadata_url: str = ""):
        self.idp_metadata_url = idp_metadata_url

    def authenticate(self, credential: dict) -> AuthenticatedPrincipal:
        raise NotImplementedError("SAML provider not yet implemented (S2.3 stub)")


class OIDCAuthProvider(AuthProvider):
    method = AuthMethod.OIDC

    def __init__(self, *, discovery_url: str = "", client_id: str = ""):
        self.discovery_url = discovery_url
        self.client_id = client_id

    def authenticate(self, credential: dict) -> AuthenticatedPrincipal:
        raise NotImplementedError("OIDC provider not yet implemented (S2.3 stub)")


class AuthProviderRegistry:
    """Pluggable lookup so the identity service can support many methods at once."""

    def __init__(self):
        self._providers: Dict[AuthMethod, AuthProvider] = {}

    def register(self, provider: AuthProvider) -> None:
        self._providers[provider.method] = provider

    def get(self, method: AuthMethod) -> Optional[AuthProvider]:
        return self._providers.get(method)

    def methods(self) -> List[AuthMethod]:
        return list(self._providers.keys())
