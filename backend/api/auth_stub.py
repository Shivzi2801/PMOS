"""
auth_stub.py
============

Authentication **abstraction**. Real auth is intentionally NOT implemented
in S2.1, but every seam a future implementation needs is defined here so that
turning auth on later is a configuration/strategy swap, not a refactor.

Why this file exists
--------------------
Enterprise customers will require JWT, OAuth2, SSO (SAML/OIDC), and API-key
auth. If route handlers reached for a concrete auth mechanism directly, every
endpoint would have to change when we adopt real auth. Instead:

* ``AuthProvider`` is an abstract strategy interface.
* Concrete stubs (``JWTAuthProvider``, ``OAuthAuthProvider``,
  ``SSOAuthProvider``, ``APIKeyAuthProvider``) declare the *shape* of each
  future mechanism and currently allow requests through as an anonymous
  principal.
* ``get_auth_provider`` is a FastAPI dependency. Swapping the active provider
  (or making it deny-by-default) is a one-line change.

The contract every provider honors:
    authenticate(headers) -> Principal     (raises AuthError on failure)
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional


class AuthError(Exception):
    """Raised when authentication fails. Mapped to 401 by error_handlers."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Principal:
    """The authenticated identity attached to a request."""

    subject: str
    scheme: str
    scopes: List[str] = field(default_factory=list)
    claims: Dict[str, object] = field(default_factory=dict)

    @property
    def is_anonymous(self) -> bool:
        return self.subject == "anonymous"


ANONYMOUS = Principal(subject="anonymous", scheme="none", scopes=["public"])


class AuthProvider(abc.ABC):
    """Strategy interface every auth mechanism must implement."""

    scheme: str = "abstract"

    @abc.abstractmethod
    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        """Return a Principal or raise AuthError."""


class _StubMixin:
    """
    Shared stub behavior: parse the relevant header if present, otherwise fall
    through to anonymous. No signature verification, no token introspection —
    that is the job of the future real implementation.
    """

    scheme = "stub"

    def _anon(self) -> Principal:
        return ANONYMOUS


class JWTAuthProvider(_StubMixin, AuthProvider):
    """Future: verify a signed JWT in ``Authorization: Bearer <token>``."""

    scheme = "jwt"

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        token = _bearer_token(headers)
        if token is None:
            return self._anon()
        # FUTURE: verify signature, exp, aud, iss; build Principal from claims.
        return Principal(subject="jwt-stub", scheme=self.scheme, scopes=["public"])


class OAuthAuthProvider(_StubMixin, AuthProvider):
    """Future: validate an OAuth2 access token via introspection/userinfo."""

    scheme = "oauth2"

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        token = _bearer_token(headers)
        if token is None:
            return self._anon()
        # FUTURE: call introspection endpoint, cache result, map scopes.
        return Principal(subject="oauth-stub", scheme=self.scheme, scopes=["public"])


class SSOAuthProvider(_StubMixin, AuthProvider):
    """Future: trust an SSO gateway header (SAML/OIDC assertion)."""

    scheme = "sso"

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        sso_user = headers.get("x-sso-user")
        if not sso_user:
            return self._anon()
        # FUTURE: validate signed assertion / gateway shared secret.
        return Principal(subject=f"sso:{sso_user}", scheme=self.scheme, scopes=["public"])


class APIKeyAuthProvider(_StubMixin, AuthProvider):
    """Future: look up ``X-API-Key`` against a key store with scopes/quotas."""

    scheme = "api_key"

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        key = headers.get("x-api-key")
        if not key:
            return self._anon()
        # FUTURE: hash + lookup key, attach owner + scopes + quota.
        return Principal(subject="apikey-stub", scheme=self.scheme, scopes=["public"])


class AllowAllAuthProvider(_StubMixin, AuthProvider):
    """Default S2.1 provider: every request is anonymous and allowed."""

    scheme = "none"

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        return self._anon()


def _bearer_token(headers: Mapping[str, str]) -> Optional[str]:
    auth = headers.get("authorization") or headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


# The active provider for this slice. Swap here (or via DI) to enable real auth.
_ACTIVE_PROVIDER: AuthProvider = AllowAllAuthProvider()


def set_auth_provider(provider: AuthProvider) -> None:
    """Replace the active auth strategy (used by config/tests)."""
    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = provider


def get_auth_provider() -> AuthProvider:
    """FastAPI dependency returning the active auth provider."""
    return _ACTIVE_PROVIDER
