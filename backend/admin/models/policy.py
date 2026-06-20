"""Policy domain models: base Policy plus access, retention and governance kinds."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ._base import (
    DomainModel,
    PolicyEffect,
    PolicyType,
    new_id,
    utcnow,
)


@dataclass
class Policy(DomainModel):
    """A named, evaluable policy.

    A policy is a list of rules. Each rule is a mapping describing a condition
    over an evaluation context plus an effect. The :class:`PolicyEngine`
    interprets these; the model itself only stores and validates structure.

    Rule shape (interpreted by the engine)::

        {
            "match": {"action": "read", "resource": "document"},
            "when": {"attr": "classification", "op": "in", "value": ["public"]},
            "effect": "allow"
        }
    """

    name: str = ""
    id: str = field(default_factory=lambda: new_id("pol"))
    type: PolicyType = PolicyType.ACCESS
    description: str = ""
    enabled: bool = True
    priority: int = 100  # lower number = evaluated first
    default_effect: PolicyEffect = PolicyEffect.DENY
    rules: list[dict[str, Any]] = field(default_factory=list)
    tenant_id: Optional[str] = None  # None => platform-wide
    workspace_id: Optional[str] = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    updated_by: Optional[str] = None


@dataclass
class AccessPolicy(Policy):
    """Access-control policy (who may perform which action on which resource)."""

    type: PolicyType = PolicyType.ACCESS
    default_effect: PolicyEffect = PolicyEffect.DENY


@dataclass
class RetentionPolicy(Policy):
    """Data-retention policy.

    ``retention_days`` is the canonical retention window. ``rules`` may scope
    different windows per resource class; when present they override the
    top-level default. A value of 0 means "retain indefinitely".
    """

    type: PolicyType = PolicyType.RETENTION
    default_effect: PolicyEffect = PolicyEffect.ALLOW
    retention_days: int = 0
    hard_delete: bool = False  # if True, purge rather than soft-delete on expiry


@dataclass
class GovernanceRule(DomainModel):
    """A single governance constraint registered with the governance framework.

    Distinct from :class:`Policy`: governance rules express *invariants the
    platform must uphold* (e.g. "PII workspaces must enable encryption") and are
    evaluated as part of compliance reporting and pre-action validation, rather
    than per-request access decisions.
    """

    name: str = ""
    id: str = field(default_factory=lambda: new_id("gov"))
    description: str = ""
    enabled: bool = True
    # A declarative predicate over a subject dict. Interpreted by GovernanceService.
    condition: dict[str, Any] = field(default_factory=dict)
    severity: str = "warning"  # GovernanceSeverity value
    remediation: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
