"""Configuration registry: persistence, versioning and history.

The registry is the system of record for configuration documents. It is
storage-agnostic: the default implementation keeps everything in memory, but
the contract (:class:`ConfigurationRegistry`) is what services depend on, so a
database-backed implementation can be substituted without touching callers.

Key responsibilities:
    * store the current version of each configuration document;
    * retain an append-only history of every prior version (for rollback and
      audit);
    * enforce optimistic concurrency via expected-version checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Generic, Optional, TypeVar

from .errors import (
    ConfigurationNotFoundError,
    ConfigurationVersionConflictError,
)
from .models.configuration import _VersionedConfig

C = TypeVar("C", bound=_VersionedConfig)


def _checksum(settings: dict) -> str:
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class ConfigurationRegistry(Generic[C]):
    """In-memory, thread-safe versioned store for one configuration kind.

    A separate registry instance is used per configuration scope (system,
    tenant, workspace). Documents are keyed by an opaque string id chosen by the
    caller (e.g. tenant_id or workspace_id), independent of the model's own
    ``id`` field, so lookups are by domain identity.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: dict[str, C] = {}
        self._history: dict[str, list[C]] = {}

    # -- reads ------------------------------------------------------------- #
    def get(self, key: str) -> C:
        with self._lock:
            cfg = self._current.get(key)
            if cfg is None:
                raise ConfigurationNotFoundError(
                    f"configuration '{key}' not found",
                    details={"key": key},
                )
            return copy.deepcopy(cfg)

    def get_or_none(self, key: str) -> Optional[C]:
        with self._lock:
            cfg = self._current.get(key)
            return copy.deepcopy(cfg) if cfg is not None else None

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._current

    def list_keys(self) -> list[str]:
        with self._lock:
            return list(self._current.keys())

    def history(self, key: str) -> list[C]:
        with self._lock:
            if key not in self._history:
                raise ConfigurationNotFoundError(
                    f"no history for configuration '{key}'", details={"key": key}
                )
            return [copy.deepcopy(c) for c in self._history[key]]

    def get_version(self, key: str, version: int) -> C:
        with self._lock:
            for cfg in self._history.get(key, []):
                if cfg.version == version:
                    return copy.deepcopy(cfg)
            # current may be newer than last archived snapshot
            cur = self._current.get(key)
            if cur is not None and cur.version == version:
                return copy.deepcopy(cur)
        raise ConfigurationNotFoundError(
            f"version {version} of configuration '{key}' not found",
            details={"key": key, "version": version},
        )

    # -- writes ------------------------------------------------------------ #
    def create(self, key: str, config: C) -> C:
        with self._lock:
            if key in self._current:
                raise ConfigurationVersionConflictError(
                    f"configuration '{key}' already exists",
                    details={"key": key},
                )
            config.version = 1
            config.checksum = _checksum(config.settings)
            self._current[key] = copy.deepcopy(config)
            self._history[key] = [copy.deepcopy(config)]
            return copy.deepcopy(config)

    def update(self, key: str, config: C, *, expected_version: int) -> C:
        """Replace the current document, bumping the version.

        Raises:
            ConfigurationVersionConflictError: if ``expected_version`` does not
                match the stored current version (optimistic concurrency).
        """
        with self._lock:
            current = self._current.get(key)
            if current is None:
                raise ConfigurationNotFoundError(
                    f"configuration '{key}' not found", details={"key": key}
                )
            if current.version != expected_version:
                raise ConfigurationVersionConflictError(
                    "version conflict during configuration update",
                    details={
                        "key": key,
                        "expected": expected_version,
                        "actual": current.version,
                    },
                )
            config.version = current.version + 1
            config.created_at = current.created_at
            config.checksum = _checksum(config.settings)
            self._current[key] = copy.deepcopy(config)
            self._history[key].append(copy.deepcopy(config))
            return copy.deepcopy(config)

    def rollback(self, key: str, target_version: int) -> C:
        """Restore a prior version as a *new* version (history is preserved).

        Rolling back never rewrites history; it appends a new version whose
        settings equal the target version's settings. This keeps the audit
        trail intact and idempotent under repeated rollbacks.
        """
        with self._lock:
            target = self.get_version(key, target_version)
            current = self._current[key]
            restored = copy.deepcopy(target)
            restored.version = current.version + 1
            restored.created_at = current.created_at
            restored.checksum = _checksum(restored.settings)
            self._current[key] = copy.deepcopy(restored)
            self._history[key].append(copy.deepcopy(restored))
            return copy.deepcopy(restored)

    def delete(self, key: str) -> None:
        with self._lock:
            self._current.pop(key, None)
            # history retained intentionally for audit; drop if compliance requires
