"""
API key manager.

Owns the full API-key lifecycle: creation, validation, rotation, revocation, and
usage auditing. The manager is the only component that ever sees raw secrets,
and only transiently — it stores salted hashes and returns the raw secret to the
caller exactly once at creation/rotation.

Validation is the hot path (runs on every machine request), so it parses the
embedded key_id out of the token to do an O(1) lookup, then verifies the hash in
constant time. Every validation — success or failure — is auditable and feeds
the `api_key_usage` metric.
"""

from __future__ import annotations

import threading
from time import time
from typing import Dict, List, Optional, Tuple

from .api_key import APIKey, APIKeyStatus, generate_secret, hash_secret
from .errors import InvalidAPIKeyError


class APIKeyManager:
    def __init__(self, *, audit=None, metrics=None):
        self._keys: Dict[str, APIKey] = {}
        self._lock = threading.RLock()
        self._audit = audit
        self._metrics = metrics

    # --- creation -----------------------------------------------------------
    def create(self, tenant_id: str, name: str, *, roles: List[str] = None,
               workspace_id: Optional[str] = None,
               ttl_seconds: Optional[float] = None) -> Tuple[APIKey, str]:
        """
        Create a key. Returns (record, raw_secret). The raw_secret is the ONLY
        time the full token is available; the caller must hand it to the client
        and never persist it.
        """
        key_id, raw = generate_secret()
        with self._lock:
            record = APIKey(
                tenant_id=tenant_id, name=name, key_id=key_id,
                workspace_id=workspace_id, roles=roles or [],
                expires_at=(time() + ttl_seconds) if ttl_seconds else None,
            )
            record.secret_hash = hash_secret(raw, record.salt)
            self._keys[key_id] = record
        self._record("api_key_created", tenant_id, key_id, True)
        return record, raw

    # --- validation (hot path) ---------------------------------------------
    def validate(self, raw_secret: str) -> APIKey:
        key_id = self._parse_key_id(raw_secret)
        with self._lock:
            record = self._keys.get(key_id) if key_id else None
            # Generic failure for any of: malformed, unknown, revoked, expired,
            # bad secret — never leak which, to avoid key enumeration.
            if record is None or not record.is_usable or \
                    not record.verify(raw_secret):
                self._record("api_key_validation_failed",
                             getattr(record, "tenant_id", None), key_id, False)
                raise InvalidAPIKeyError("invalid or inactive API key")
            record.last_used_at = time()
        if self._metrics:
            self._metrics.incr_api_key_usage(record.key_id)
        self._record("api_key_used", record.tenant_id, record.key_id, True)
        return record

    # --- rotation -----------------------------------------------------------
    def rotate(self, key_id: str) -> Tuple[APIKey, str]:
        """
        Issue a replacement key inheriting tenant/workspace/roles, mark the old
        one ROTATED (still resolvable for audit, but no longer usable).
        """
        with self._lock:
            old = self._keys.get(key_id)
            if old is None:
                raise InvalidAPIKeyError("unknown key", detail={"key_id": key_id})
            new_record, raw = self.create(
                old.tenant_id, f"{old.name} (rotated)", roles=list(old.roles),
                workspace_id=old.workspace_id,
            )
            old.status = APIKeyStatus.ROTATED
            old.rotated_to = new_record.key_id
        self._record("api_key_rotated", old.tenant_id, key_id, True)
        return new_record, raw

    # --- revocation ---------------------------------------------------------
    def revoke(self, key_id: str) -> bool:
        with self._lock:
            record = self._keys.get(key_id)
            if record is None:
                return False
            record.status = APIKeyStatus.REVOKED
        self._record("api_key_revoked", record.tenant_id, key_id, True)
        return True

    # --- queries ------------------------------------------------------------
    def get(self, key_id: str) -> Optional[APIKey]:
        with self._lock:
            return self._keys.get(key_id)

    def list_for_tenant(self, tenant_id: str) -> List[dict]:
        with self._lock:
            return [k.to_dict() for k in self._keys.values()
                    if k.tenant_id == tenant_id]

    # --- helpers ------------------------------------------------------------
    @staticmethod
    def _parse_key_id(raw_secret: str) -> Optional[str]:
        # token format: pmos_sk_<key_id>_<entropy>  where key_id == "key_<hex>"
        try:
            parts = raw_secret.split("_")
            # ["pmos","sk","key","<hex>","<entropy>"]
            if len(parts) >= 5 and parts[0] == "pmos" and parts[2] == "key":
                return f"key_{parts[3]}"
        except Exception:  # noqa: BLE001
            return None
        return None

    def _record(self, action, tenant_id, key_id, allowed):
        if self._audit:
            self._audit.record(action=action, tenant_id=tenant_id,
                               allowed=allowed, detail={"key_id": key_id})
