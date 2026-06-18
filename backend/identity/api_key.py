"""
API key model.

An APIKey is a long-lived, tenant-scoped credential for machine-to-machine
access (CI pipelines, server integrations, the orchestration layer running
scheduled jobs). The raw secret is shown to the user exactly once at creation;
PMOS stores only a salted hash, so a database compromise never leaks usable
keys.

Lifecycle states: ACTIVE -> (ROTATED | REVOKED) and EXPIRED (time-based). Each
key carries its own role set so a key can be least-privilege (e.g. an ingestion
pipeline key that can only `ingest_documents`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import List, Optional, Tuple

_PREFIX = "pmos_sk"   # visible, non-secret prefix to make keys identifiable


class APIKeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    ROTATED = "rotated"   # superseded by a newer key; kept for audit


def _new_id() -> str:
    return f"key_{uuid.uuid4().hex[:16]}"


def generate_secret() -> Tuple[str, str]:
    """
    Return (key_id, raw_secret). raw_secret is the full token shown once to the
    client; it embeds the key_id so the manager can locate the record on
    validation without scanning every key.
    """
    key_id = _new_id()
    entropy = secrets.token_urlsafe(32)
    raw = f"{_PREFIX}_{key_id}_{entropy}"
    return key_id, raw


def hash_secret(raw_secret: str, salt: str) -> str:
    """Salted SHA-256. Constant-time comparison is done by the manager."""
    return hashlib.sha256(f"{salt}:{raw_secret}".encode()).hexdigest()


@dataclass
class APIKey:
    tenant_id: str
    name: str
    key_id: str = field(default_factory=_new_id)
    workspace_id: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    salt: str = field(default_factory=lambda: secrets.token_hex(16))
    secret_hash: str = ""
    status: APIKeyStatus = APIKeyStatus.ACTIVE
    created_at: float = field(default_factory=time)
    expires_at: Optional[float] = None
    last_used_at: Optional[float] = None
    rotated_to: Optional[str] = None  # key_id of the replacement

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and time() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.status == APIKeyStatus.ACTIVE and not self.is_expired

    def verify(self, raw_secret: str) -> bool:
        candidate = hash_secret(raw_secret, self.salt)
        return hmac.compare_digest(candidate, self.secret_hash)

    def to_dict(self, *, include_secret_hash: bool = False) -> dict:
        d = {
            "key_id": self.key_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "roles": list(self.roles),
            "status": self.status.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "rotated_to": self.rotated_to,
        }
        if include_secret_hash:
            d["secret_hash"] = self.secret_hash
        return d
