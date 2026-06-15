"""The QuarantineRecord contract — a held-back, unsafe record."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .canonical_document import Provenance
from .pii_finding import PIIFinding
from .injection_finding import InjectionFinding


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class QuarantineRecord:
    """A suspicious record removed from the safe stream and stored for review.

    Preserves BOTH provenance and the original raw payload so an operator can
    inspect exactly what arrived and, if it is a false positive, re-ingest it.
    """

    provenance: Provenance
    reason: str                                   # human-readable quarantine reason
    original_payload: Dict[str, Any]              # raw connector record, verbatim
    quarantine_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    quarantined_at: str = field(default_factory=_utc_now_iso)
    injection_finding: Optional[InjectionFinding] = None
    pii_findings: List[PIIFinding] = field(default_factory=list)
    # Canonical doc captured at quarantine time (may be None if normalization failed).
    canonical_snapshot: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quarantine_id": self.quarantine_id,
            "quarantined_at": self.quarantined_at,
            "reason": self.reason,
            "provenance": self.provenance.to_dict(),
            "original_payload": self.original_payload,
            "injection_finding": (
                self.injection_finding.to_dict() if self.injection_finding else None
            ),
            "pii_findings": [f.to_dict() for f in self.pii_findings],
            "canonical_snapshot": self.canonical_snapshot,
        }
