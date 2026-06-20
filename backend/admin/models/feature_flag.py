"""Feature flag domain model with multi-level overrides and rollout support."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ._base import DomainModel, new_id, utcnow


@dataclass
class FeatureFlag(DomainModel):
    """A toggleable feature with layered resolution.

    Resolution order, highest priority first:
        1. workspace override (``workspace_overrides[workspace_id]``)
        2. tenant override (``tenant_overrides[tenant_id]``)
        3. percentage rollout (deterministic per subject key)
        4. global ``enabled`` default

    The rollout uses a stable hash of ``(key, subject_key)`` so a given subject
    consistently lands inside or outside the rollout bucket across calls, while
    different flags produce independent distributions.
    """

    key: str = ""
    id: str = field(default_factory=lambda: new_id("flag"))
    description: str = ""
    enabled: bool = False
    rollout_percentage: float = 0.0  # 0..100, applied when no override matches
    tenant_overrides: dict[str, bool] = field(default_factory=dict)
    workspace_overrides: dict[str, bool] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    updated_by: Optional[str] = None

    def evaluate(
        self,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_key: Optional[str] = None,
    ) -> bool:
        """Return the effective on/off state for the given context."""
        if workspace_id is not None and workspace_id in self.workspace_overrides:
            return self.workspace_overrides[workspace_id]
        if tenant_id is not None and tenant_id in self.tenant_overrides:
            return self.tenant_overrides[tenant_id]
        if self.rollout_percentage > 0:
            bucket = self._bucket(subject_key or tenant_id or workspace_id or "")
            if bucket < self.rollout_percentage:
                return True
            # below rollout threshold falls back to the global default
        return self.enabled

    def _bucket(self, subject_key: str) -> float:
        """Map a subject to a stable bucket in [0, 100)."""
        digest = hashlib.sha256(f"{self.key}:{subject_key}".encode()).hexdigest()
        # take first 8 hex chars -> int -> scale to [0,100)
        return (int(digest[:8], 16) % 10_000) / 100.0
