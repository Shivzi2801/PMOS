"""Audit, admin-action and platform-health domain models."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ._base import (
    AuditCategory,
    DomainModel,
    HealthStatus,
    new_id,
    utcnow,
)


@dataclass
class AdminAction(DomainModel):
    """Describes an administrative operation requested by a principal.

    This is the *intent* record produced before/around an action. It is the
    canonical input to the audit service, which derives an :class:`AuditEvent`
    from it. Keeping intent and audit separate lets services log attempts
    (including failures) distinctly from successful state changes.
    """

    action: str = ""  # e.g. "tenant.suspend"
    id: str = field(default_factory=lambda: new_id("act"))
    actor_id: str = ""  # principal performing the action
    actor_type: str = "user"  # user | service | system
    target_type: str = ""  # tenant | workspace | policy | config | flag | quota
    target_id: str = ""
    tenant_id: Optional[str] = None
    workspace_id: Optional[str] = None
    parameters: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utcnow)


@dataclass
class AuditEvent(DomainModel):
    """An immutable record of something that happened on the platform.

    Audit events form a hash-chained, append-only log. Each event stores the
    ``previous_hash`` of the prior event and its own ``event_hash`` computed
    over the canonical content. This makes tampering detectable: altering any
    historical event breaks the chain from that point forward.
    """

    category: AuditCategory = AuditCategory.ADMIN_ACTION
    action: str = ""
    id: str = field(default_factory=lambda: new_id("evt"))
    actor_id: str = ""
    actor_type: str = "user"
    target_type: str = ""
    target_id: str = ""
    tenant_id: Optional[str] = None
    workspace_id: Optional[str] = None
    outcome: str = "success"  # success | failure | denied
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # Snapshots for change events (configuration/policy diffs).
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
    timestamp: datetime = field(default_factory=utcnow)
    sequence: int = 0
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None

    def compute_hash(self) -> str:
        """Deterministic content hash for chain integrity.

        Excludes ``event_hash`` itself but includes ``previous_hash`` so the
        chain is bound together.
        """
        payload = {
            "id": self.id,
            "category": self.category.value,
            "action": self.action,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "outcome": self.outcome,
            "message": self.message,
            "metadata": self.metadata,
            "before": self.before,
            "after": self.after,
            "timestamp": self.timestamp.isoformat(),
            "sequence": self.sequence,
            "previous_hash": self.previous_hash,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass
class PlatformHealth(DomainModel):
    """Aggregated health snapshot of the platform and its dependencies."""

    status: HealthStatus = HealthStatus.UNKNOWN
    id: str = field(default_factory=lambda: new_id("health"))
    components: dict[str, str] = field(default_factory=dict)  # name -> status value
    dependencies: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=utcnow)
