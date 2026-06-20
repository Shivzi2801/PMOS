"""Governance framework: rule registration, validation, violation detection.

Governance rules express invariants the platform must uphold (e.g. "PII
workspaces must enable encryption", "production tenants must define a request
quota"). Unlike the access-oriented PolicyEngine, governance rules are evaluated
as *pre-action validation* and as *periodic compliance reporting*.

A rule's ``condition`` is a declarative predicate over a subject mapping. When
the condition evaluates True, the subject is considered to *violate* the rule
(i.e. conditions describe the bad state). This keeps rules readable:

    name: "encryption_required_for_pii"
    condition: {all: [
        {attr: "settings.classification", op: "eq", value: "pii"},
        {attr: "settings.encryption_enabled", op: "ne", value: True},
    ]}

Severity drives whether a violation blocks (``critical``) or merely warns.
"""
from __future__ import annotations

import threading
from typing import Any, Mapping, Optional

from .audit_service import AuditService
from .errors import GovernanceError, GovernanceViolationError
from .metrics import GOVERNANCE_VIOLATIONS, get_metrics
from .models import AuditCategory, GovernanceRule, GovernanceSeverity


def _get_attr(subject: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    cur: Any = subject
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _eval_leaf(cond: Mapping[str, Any], subject: Mapping[str, Any]) -> bool:
    attr = cond.get("attr")
    op = cond.get("op", "eq")
    expected = cond.get("value")
    found, actual = _get_attr(subject, attr) if attr else (False, None)
    if op == "exists":
        return found
    if op == "absent":
        return not found
    if not found:
        # attribute missing: most comparisons are False, except 'ne'/'not_in'
        return op in ("ne", "not_in")
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
    except TypeError:
        return False
    raise GovernanceError(f"unsupported governance op '{op}'", details={"op": op})


def _eval_condition(cond: Mapping[str, Any], subject: Mapping[str, Any]) -> bool:
    """Evaluate a (possibly nested) boolean condition tree."""
    if "all" in cond:
        return all(_eval_condition(c, subject) for c in cond["all"])
    if "any" in cond:
        return any(_eval_condition(c, subject) for c in cond["any"])
    if "not" in cond:
        return not _eval_condition(cond["not"], subject)
    return _eval_leaf(cond, subject)


class GovernanceService:
    """Register governance rules and evaluate subjects against them."""

    def __init__(self, *, audit: Optional[AuditService] = None) -> None:
        self._lock = threading.RLock()
        self._rules: dict[str, GovernanceRule] = {}
        self._audit = audit

    def register_rule(self, rule: GovernanceRule, *, actor_id: str = "system") -> GovernanceRule:
        if not rule.name or not rule.name.strip():
            raise GovernanceError("governance rule must have a name")
        if not rule.condition:
            raise GovernanceError(
                "governance rule must declare a condition", details={"rule": rule.name}
            )
        with self._lock:
            self._rules[rule.id] = rule
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.POLICY,
                action="governance.register_rule",
                actor_id=actor_id,
                target_type="governance_rule",
                target_id=rule.id,
                after=rule.to_dict(),
            )
        return rule

    def unregister_rule(self, rule_id: str, *, actor_id: str = "system") -> None:
        with self._lock:
            rule = self._rules.pop(rule_id, None)
        if rule is None:
            raise GovernanceError(
                f"governance rule '{rule_id}' not found", details={"rule_id": rule_id}
            )
        if self._audit is not None:
            self._audit.record(
                category=AuditCategory.POLICY,
                action="governance.unregister_rule",
                actor_id=actor_id,
                target_type="governance_rule",
                target_id=rule_id,
                before=rule.to_dict(),
            )

    def list_rules(self, *, enabled_only: bool = False) -> list[GovernanceRule]:
        with self._lock:
            rules = list(self._rules.values())
        return [r for r in rules if (not enabled_only or r.enabled)]

    def validate(self, subject: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Evaluate a subject against all enabled rules; return violations.

        Does *not* raise. Each violation is a dict with rule id/name/severity
        and remediation guidance. Used for compliance reporting and as the basis
        for :meth:`enforce`.
        """
        violations: list[dict[str, Any]] = []
        for rule in self.list_rules(enabled_only=True):
            try:
                violated = _eval_condition(rule.condition, subject)
            except GovernanceError:
                raise
            except Exception as exc:  # malformed condition data
                raise GovernanceError(
                    f"failed to evaluate rule '{rule.name}'",
                    details={"rule_id": rule.id, "detail": str(exc)},
                ) from exc
            if violated:
                violations.append(
                    {
                        "rule_id": rule.id,
                        "name": rule.name,
                        "severity": rule.severity,
                        "description": rule.description,
                        "remediation": rule.remediation,
                    }
                )
        for v in violations:
            get_metrics().increment(GOVERNANCE_VIOLATIONS, severity=v["severity"])
        return violations

    def enforce(self, subject: Mapping[str, Any], *, actor_id: str = "system") -> None:
        """Validate and *block* if any critical violation is present.

        Warnings/info violations are recorded but allowed; critical violations
        raise :class:`GovernanceViolationError`.
        """
        violations = self.validate(subject)
        critical = [v for v in violations if v["severity"] == GovernanceSeverity.CRITICAL.value]
        if violations and self._audit is not None:
            self._audit.record(
                category=AuditCategory.SECURITY,
                action="governance.violation_detected",
                actor_id=actor_id,
                target_type=subject.get("type", "unknown"),
                target_id=str(subject.get("workspace_id") or subject.get("tenant_id") or ""),
                tenant_id=subject.get("tenant_id"),
                workspace_id=subject.get("workspace_id"),
                outcome="denied" if critical else "warning",
                metadata={"violations": violations},
            )
        if critical:
            raise GovernanceViolationError(
                "subject violates one or more critical governance rules",
                violations=critical,
            )

    def compliance_report(
        self, subjects: list[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Produce a compliance report across many subjects.

        Returns aggregate counts plus a per-subject breakdown. Useful for
        periodic governance dashboards and audit exports.
        """
        per_subject = []
        total_violations = 0
        critical_count = 0
        for subject in subjects:
            violations = self.validate(subject)
            total_violations += len(violations)
            critical_count += sum(
                1 for v in violations if v["severity"] == GovernanceSeverity.CRITICAL.value
            )
            per_subject.append(
                {
                    "subject_id": subject.get("workspace_id")
                    or subject.get("tenant_id"),
                    "type": subject.get("type", "unknown"),
                    "compliant": not violations,
                    "violations": violations,
                }
            )
        return {
            "total_subjects": len(subjects),
            "compliant_subjects": sum(1 for s in per_subject if s["compliant"]),
            "total_violations": total_violations,
            "critical_violations": critical_count,
            "subjects": per_subject,
        }
