"""Quarantine service — builds and persists QuarantineRecords."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts import (
    CanonicalDocument,
    InjectionFinding,
    PIIFinding,
    Provenance,
    QuarantineRecord,
)
from .store import QuarantineStore, InMemoryQuarantineStore


class QuarantineService:
    """Encapsulates the policy and mechanics of quarantining a record."""

    def __init__(self, store: Optional[QuarantineStore] = None) -> None:
        self._store = store or InMemoryQuarantineStore()

    def quarantine(
        self,
        *,
        provenance: Provenance,
        reason: str,
        original_payload: Dict[str, Any],
        injection_finding: Optional[InjectionFinding] = None,
        pii_findings: Optional[List[PIIFinding]] = None,
        canonical: Optional[CanonicalDocument] = None,
    ) -> QuarantineRecord:
        record = QuarantineRecord(
            provenance=provenance,
            reason=reason,
            original_payload=original_payload,
            injection_finding=injection_finding,
            pii_findings=list(pii_findings or []),
            canonical_snapshot=canonical.to_dict() if canonical else None,
        )
        self._store.save(record)
        return record

    def get(self, quarantine_id: str) -> Optional[QuarantineRecord]:
        return self._store.get(quarantine_id)

    def list(self) -> List[QuarantineRecord]:
        return self._store.list()

    def count(self) -> int:
        return self._store.count()
