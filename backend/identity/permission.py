"""
Permission model.

A Permission is an atomic, named capability ("verb on a resource class") that a
role can grant. Permissions are the extensibility surface of the RBAC system:
new product features add new permission constants without touching the engine.

The seed set covers the capabilities named in the spec. A wildcard (`*`) is
supported for the SYSTEM/ADMIN super-roles so a role can hold "all permissions"
without enumerating them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Set

WILDCARD = "*"


@dataclass(frozen=True)
class Permission:
    """An atomic capability. Frozen so it is hashable and usable in sets."""

    name: str
    description: str = ""

    def __str__(self) -> str:  # pragma: no cover
        return self.name


class Permissions:
    """
    Central registry of known permissions.

    Extensible: downstream slices can call `register()` to add their own
    (e.g. the connectors slice could register "manage_salesforce_connector").
    """

    VIEW_DOCUMENTS = "view_documents"
    INGEST_DOCUMENTS = "ingest_documents"
    MANAGE_CONNECTORS = "manage_connectors"
    MANAGE_USERS = "manage_users"
    MANAGE_API_KEYS = "manage_api_keys"
    VIEW_METRICS = "view_metrics"
    ADMIN_ACCESS = "admin_access"
    # Workflow-execution permissions (consumed by orchestration integration).
    RUN_QUERY = "run_query"
    RUN_INGESTION = "run_ingestion"
    RUN_REINDEX = "run_reindex"

    _REGISTRY: Set[str] = set()

    @classmethod
    def seed(cls) -> Set[str]:
        base = {
            cls.VIEW_DOCUMENTS, cls.INGEST_DOCUMENTS, cls.MANAGE_CONNECTORS,
            cls.MANAGE_USERS, cls.MANAGE_API_KEYS, cls.VIEW_METRICS,
            cls.ADMIN_ACCESS, cls.RUN_QUERY, cls.RUN_INGESTION, cls.RUN_REINDEX,
        }
        cls._REGISTRY |= base
        return set(cls._REGISTRY)

    @classmethod
    def register(cls, name: str) -> str:
        """Add a new permission to the known set (idempotent)."""
        cls._REGISTRY.add(name)
        return name

    @classmethod
    def all(cls) -> Set[str]:
        if not cls._REGISTRY:
            cls.seed()
        return set(cls._REGISTRY)

    @classmethod
    def is_known(cls, name: str) -> bool:
        return name == WILDCARD or name in cls.all()


# Ensure the registry is populated at import time.
Permissions.seed()
