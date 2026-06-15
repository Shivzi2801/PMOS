"""Quarantine service.

Stores suspicious records with full provenance, original payload, and a
quarantine reason for later operator review or re-ingestion.
"""

from .store import QuarantineStore, InMemoryQuarantineStore, FileQuarantineStore
from .service import QuarantineService

__all__ = [
    "QuarantineStore",
    "InMemoryQuarantineStore",
    "FileQuarantineStore",
    "QuarantineService",
]
