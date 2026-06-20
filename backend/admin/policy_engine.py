"""Policy engine: registration and evaluation of policies.

The engine evaluates declarative policies against a runtime *context* (a flat
or nested mapping of attributes). It supports:

  * access policies — produce an allow/deny decision for an action+resource;
  * retention policies — resolve the retention window for a resource;
  * governance policies — surface matching governance constraints;
  * compliance checks — evaluate a subject against all applicable policies.

Rule evaluation is intentionally simple and side-effect free so decisions are
deterministic and explainable. Each rule may declare:

    match: dict of context attrs that must equal the given values to apply
    when:  a single condition {attr, op, value} (optional)
    effect: "allow" | "deny"

Supported ``op`` values: eq, ne, in, not_in, gt, gte, lt, lte, contains, exists.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from .audit_service import AuditService
from .errors import (
    PolicyEvaluationError,
    PolicyNotFoundError,
)
from .metrics import POLICY_EVALUATIONS, get_metrics
from .models import (
    AccessPolicy,
    AuditCategory,
    Policy,
    PolicyEffect,
    PolicyType,
    RetentionPolicy,
)


@dataclass
class PolicyDecision:
    """The outcome of an access evaluation."""

    allowed: bool
    effect: PolicyEffect
    matched_policy_id: Optional[str]
    matched_rule_index: Optional[int]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "effect": self.effect.value,
            "matched_policy_id": self.matched_policy_id,
            "matched_rule_index": self.matched_rule_index,
            "reason": self.reason,
        }


def _get_attr(context: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """Resolve a dotted attribute path. Returns (found, value)."""
    cur: Any = context
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _eval_condition(cond: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    attr = cond.get("attr")
    op = cond.get("op", "eq")
    expected = cond.get("value")
    if attr is None:
        raise PolicyEvaluationError("condition missing 'attr'", details={"cond": dict(cond)})
    found, actual = _get_attr(context, attr)

    if op == "exists":
        return found
    if not found:
        return False
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "in":
            return actual in expected
        if op == "not_in":
            return actual not in expected
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "contains":
            return expected in actual
    except TypeError as exc:
        raise PolicyEvaluationError(
            f"type error evaluating op '{op}'", details={"attr": attr, "detail": str(exc)}
        ) from exc
    raise PolicyEvaluationError(f"unsupported op '{op}'", details={"op": op})


def _rule_matches(rule: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    match = rule.get("match", {})
    for key, value in match.items():
        found, actual = _get_attr(context, key)
        if not found or actual != value:
            return False
    when = rule.get("when")
    if when is not None and not _eval_condition(when, context):
        return False
    return True


class PolicyEngine:
    """Stores policies and evaluates them against contexts."""

    def __init__(self, *, audit: Optional[AuditService] = None) -> None:
        self._lock = threading.RLock()
        self._policies: dict[str, Policy] = {}
        self._audit = audit

    # -- registration ------------------------------------------------------ #
    def register(self, policy: Policy, *, actor_id: str = "system") -> Policy:
        with self._lock:
            self._policies[policy.id] = policy
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.POLICY,
                action="policy.register",
                actor_id=actor_id,
                target_type=policy.type.value + "_policy",
                target_id=policy.id,
                after=policy.to_dict(),
            )
        return policy

    def unregister(self, policy_id: str, *, actor_id: str = "system") -> None:
        with self._lock:
            policy = self._policies.pop(policy_id, None)
        if policy is None:
            raise PolicyNotFoundError(
                f"policy '{policy_id}' not found", details={"policy_id": policy_id}
            )
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.POLICY,
                action="policy.unregister",
                actor_id=actor_id,
                target_type=policy.type.value + "_policy",
                target_id=policy.id,
                before=policy.to_dict(),
            )

    def get(self, policy_id: str) -> Policy:
        with self._lock:
            policy = self._policies.get(policy_id)
            if policy is None:
                raise PolicyNotFoundError(
                    f"policy '{policy_id}' not found", details={"policy_id": policy_id}
                )
            return policy

    def list_policies(
        self,
        *,
        type: Optional[PolicyType] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> list[Policy]:
        with self._lock:
            policies = list(self._policies.values())
        out = []
        for p in policies:
            if type is not None and p.type != type:
                continue
            # platform-wide policies (tenant_id None) always apply; scoped ones
            # apply only when the scope matches.
            if tenant_id is not None and p.tenant_id not in (None, tenant_id):
                continue
            if workspace_id is not None and p.workspace_id not in (None, workspace_id):
                continue
            out.append(p)
        return out

    # -- access evaluation ------------------------------------------------- #
    def evaluate_access(
        self,
        context: Mapping[str, Any],
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> PolicyDecision:
        """Evaluate access policies; first explicit match wins.

        Policies are evaluated by ascending ``priority``. Within a policy, the
        first matching rule decides. If no rule matches in any policy, the
        most-specific policy's ``default_effect`` applies; absent any policy the
        decision defaults to DENY (secure default).
        """
        applicable = sorted(
            self.list_policies(
                type=PolicyType.ACCESS, tenant_id=tenant_id, workspace_id=workspace_id
            ),
            key=lambda p: p.priority,
        )
        applicable = [p for p in applicable if p.enabled]

        decision: Optional[PolicyDecision] = None
        for policy in applicable:
            for idx, rule in enumerate(policy.rules):
                if _rule_matches(rule, context):
                    effect = PolicyEffect(rule.get("effect", policy.default_effect.value))
                    decision = PolicyDecision(
                        allowed=effect == PolicyEffect.ALLOW,
                        effect=effect,
                        matched_policy_id=policy.id,
                        matched_rule_index=idx,
                        reason=f"matched rule {idx} in policy {policy.id}",
                    )
                    break
            if decision is not None:
                break

        if decision is None:
            if applicable:
                eff = applicable[0].default_effect
                decision = PolicyDecision(
                    allowed=eff == PolicyEffect.ALLOW,
                    effect=eff,
                    matched_policy_id=applicable[0].id,
                    matched_rule_index=None,
                    reason="no rule matched; applied policy default effect",
                )
            else:
                decision = PolicyDecision(
                    allowed=False,
                    effect=PolicyEffect.DENY,
                    matched_policy_id=None,
                    matched_rule_index=None,
                    reason="no applicable access policy; secure default deny",
                )

        get_metrics().increment(
            POLICY_EVALUATIONS,
            type="access",
            effect=decision.effect.value,
        )
        return decision

    # -- retention --------------------------------------------------------- #
    def resolve_retention(
        self,
        resource_class: str,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[RetentionPolicy]:
        """Return the most specific applicable retention policy, if any."""
        candidates = [
            p
            for p in self.list_policies(
                type=PolicyType.RETENTION, tenant_id=tenant_id, workspace_id=workspace_id
            )
            if p.enabled and isinstance(p, RetentionPolicy)
        ]
        if not candidates:
            get_metrics().increment(POLICY_EVALUATIONS, type="retention", effect="none")
            return None
        # specificity: workspace-scoped > tenant-scoped > platform-wide
        def specificity(p: RetentionPolicy) -> int:
            return (1 if p.tenant_id else 0) + (1 if p.workspace_id else 0)

        best = max(candidates, key=specificity)
        get_metrics().increment(POLICY_EVALUATIONS, type="retention", effect="resolved")
        return best

    # -- compliance -------------------------------------------------------- #
    def compliance_check(
        self,
        context: Mapping[str, Any],
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Evaluate all governance-typed policies against a context.

        Returns a report listing each governance policy and whether the context
        satisfied it (no DENY rule matched).
        """
        results: list[dict[str, Any]] = []
        compliant = True
        for policy in self.list_policies(
            type=PolicyType.GOVERNANCE, tenant_id=tenant_id, workspace_id=workspace_id
        ):
            if not policy.enabled:
                continue
            violated = False
            for idx, rule in enumerate(policy.rules):
                if _rule_matches(rule, context):
                    effect = PolicyEffect(rule.get("effect", policy.default_effect.value))
                    if effect == PolicyEffect.DENY:
                        violated = True
                        break
            if violated:
                compliant = False
            results.append({"policy_id": policy.id, "name": policy.name, "compliant": not violated})
        get_metrics().increment(
            POLICY_EVALUATIONS, type="compliance", effect="pass" if compliant else "fail"
        )
        return {"compliant": compliant, "policies": results}
