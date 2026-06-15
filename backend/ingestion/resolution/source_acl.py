"""Source ACL models for PMOS Wave 1 Slice 1.4.

Entity visibility is determined by the ACL attached to the source that
produced an extraction. ACL checks run *before* any entity merge so that
a denied source can never leak its evidence into a canonical entity that
another principal can read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class ACLDecision(str, enum.Enum):
    """The two terminal decisions an ACL evaluation can yield."""

    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class ACLPrincipal:
    """The subject whose access is being evaluated.

    A principal is always tenant-scoped. The optional principal_id allows
    finer-grained (user / group) rules; when omitted, only tenant-level
    rules apply.
    """

    tenant_id: str
    principal_id: Optional[str] = None


@dataclass
class SourceACL:
    """Access-control list bound to a single source.

    Visibility is tenant scoped: a source belongs to exactly one tenant,
    and cross-tenant access is always denied regardless of explicit rules.

    Within the owning tenant, explicit per-principal rules are consulted,
    falling back to ``default_decision``.

    Attributes:
        source_id: The source these rules govern.
        tenant_id: The tenant that owns the source.
        rules: principal_id -> ALLOW/DENY for explicit grants.
        default_decision: Applied when no explicit rule matches (in-tenant).
    """

    source_id: str
    tenant_id: str
    rules: Dict[str, ACLDecision] = field(default_factory=dict)
    default_decision: ACLDecision = ACLDecision.ALLOW

    def allow(self, principal_id: str) -> None:
        self.rules[principal_id] = ACLDecision.ALLOW

    def deny(self, principal_id: str) -> None:
        self.rules[principal_id] = ACLDecision.DENY

    def evaluate(self, principal: ACLPrincipal) -> ACLDecision:
        """Resolve the effective decision for ``principal``.

        Order of precedence:
          1. Cross-tenant access is always DENY.
          2. An explicit per-principal rule (DENY wins over ALLOW only if
             written as such; explicit rule is authoritative).
          3. The source default_decision.
        """
        if principal.tenant_id != self.tenant_id:
            return ACLDecision.DENY

        if principal.principal_id is not None:
            explicit = self.rules.get(principal.principal_id)
            if explicit is not None:
                return explicit

        return self.default_decision

    def is_visible(self, principal: ACLPrincipal) -> bool:
        return self.evaluate(principal) is ACLDecision.ALLOW


class ACLRegistry:
    """In-memory lookup of source_id -> SourceACL.

    The merge pipeline consults this registry to decide whether evidence
    from a source may participate in resolution for a given principal.
    Sources with no registered ACL are treated as DENY (fail closed).
    """

    def __init__(self) -> None:
        self._acls: Dict[str, SourceACL] = {}

    def register(self, acl: SourceACL) -> None:
        self._acls[acl.source_id] = acl

    def get(self, source_id: str) -> Optional[SourceACL]:
        return self._acls.get(source_id)

    def check(self, source_id: str, principal: ACLPrincipal) -> ACLDecision:
        acl = self._acls.get(source_id)
        if acl is None:
            # Fail closed: an unknown source is never visible.
            return ACLDecision.DENY
        return acl.evaluate(principal)
