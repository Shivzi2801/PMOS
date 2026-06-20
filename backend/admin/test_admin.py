"""Test suite for the Administration & Governance module (Slice S2.5).

Run with:  python -m pytest backend/admin/test_admin.py -v
Covers: configuration management, policy evaluation, quota enforcement,
feature flags, governance validation, audit logging, tenant/workspace
lifecycle, health probes and metrics.
"""
from __future__ import annotations

import pytest

from backend.admin import (
    ConfigurationService,
    ConfigurationValidator,
    FeatureFlagService,
    GovernanceService,
    HealthService,
    PolicyEngine,
    QuotaService,
    TenantAdminService,
    WorkspaceAdminService,
    AuditService,
)
from backend.admin.configuration_validator import FieldSpec
from backend.admin.errors import (
    ConfigurationValidationError,
    ConfigurationVersionConflictError,
    FeatureFlagError,
    GovernanceViolationError,
    PolicyNotFoundError,
    QuotaExceededError,
    TenantStateError,
    WorkspaceStateError,
)
from backend.admin.health_service import ComponentCheck
from backend.admin.metrics import (
    CONFIG_CHANGES,
    GOVERNANCE_VIOLATIONS,
    POLICY_EVALUATIONS,
    QUOTA_VIOLATIONS,
    InMemoryMetrics,
    set_metrics_sink,
    METRICS,
)
from backend.admin.models import (
    AccessPolicy,
    AuditCategory,
    GovernanceRule,
    GovernanceSeverity,
    HealthStatus,
    PolicyEffect,
    QuotaPeriod,
    QuotaScope,
    RetentionPolicy,
    TenantState,
    WorkspaceState,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def audit() -> AuditService:
    return AuditService()


@pytest.fixture
def metrics() -> InMemoryMetrics:
    sink = InMemoryMetrics()
    set_metrics_sink(sink)
    yield sink
    set_metrics_sink(InMemoryMetrics())


# --------------------------------------------------------------------------- #
# Configuration management
# --------------------------------------------------------------------------- #
class TestConfiguration:
    def test_create_load_update_version_rollback(self, audit):
        svc = ConfigurationService(audit=audit)
        cfg = svc.create_tenant_config("t1", {"max_docs": 100}, display_name="Acme")
        assert cfg.version == 1

        loaded = svc.load_tenant_config("t1")
        assert loaded.settings["max_docs"] == 100

        updated = svc.update_tenant_config(
            "t1", {"max_docs": 200}, expected_version=1
        )
        assert updated.version == 2
        assert updated.settings["max_docs"] == 200

        history = svc.tenant_config_history("t1")
        assert [h.version for h in history] == [1, 2]

        rolled = svc.rollback_tenant_config("t1", target_version=1)
        assert rolled.version == 3
        assert rolled.settings["max_docs"] == 100

    def test_optimistic_concurrency_conflict(self, audit):
        svc = ConfigurationService(audit=audit)
        svc.create_tenant_config("t1", {"a": 1})
        svc.update_tenant_config("t1", {"a": 2}, expected_version=1)
        with pytest.raises(ConfigurationVersionConflictError):
            svc.update_tenant_config("t1", {"a": 3}, expected_version=1)

    def test_validation_rejects_bad_settings(self):
        validator = ConfigurationValidator()
        validator.register_schema(
            "tenant",
            {
                "max_docs": FieldSpec(type=int, required=True, validator=lambda v: v > 0),
                "region": FieldSpec(type=str, choices=["us", "eu"]),
            },
        )
        svc = ConfigurationService(validator=validator)
        with pytest.raises(ConfigurationValidationError):
            svc.create_tenant_config("t1", {"max_docs": -5})
        with pytest.raises(ConfigurationValidationError):
            svc.create_tenant_config("t2", {"max_docs": 5, "region": "asia"})
        # unknown field rejected in strict mode
        with pytest.raises(ConfigurationValidationError):
            svc.create_tenant_config("t3", {"max_docs": 5, "bogus": 1})

    def test_effective_config_layering(self):
        svc = ConfigurationService()
        svc.init_system({"theme": "dark", "limits": {"a": 1, "b": 2}})
        svc.create_tenant_config("t1", {"limits": {"b": 20, "c": 30}})
        svc.create_workspace_config("w1", "t1", {"limits": {"c": 300}})
        eff = svc.effective_config(tenant_id="t1", workspace_id="w1")
        assert eff["theme"] == "dark"
        assert eff["limits"] == {"a": 1, "b": 20, "c": 300}

    def test_config_change_is_audited(self, audit):
        svc = ConfigurationService(audit=audit)
        svc.create_tenant_config("t1", {"a": 1})
        events = audit.query(category=AuditCategory.CONFIGURATION)
        assert any(e.action == "configuration.create" for e in events)


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #
class TestFeatureFlags:
    def test_enable_disable(self, audit):
        svc = FeatureFlagService(audit=audit)
        svc.create_flag("beta", enabled=False)
        assert svc.is_enabled("beta") is False
        svc.enable("beta")
        assert svc.is_enabled("beta") is True
        svc.disable("beta")
        assert svc.is_enabled("beta") is False

    def test_tenant_and_workspace_overrides(self):
        svc = FeatureFlagService()
        svc.create_flag("feat", enabled=False)
        svc.set_tenant_override("feat", "t1", True)
        svc.set_workspace_override("feat", "w1", False)
        assert svc.is_enabled("feat", tenant_id="t1") is True
        # workspace override beats tenant override
        assert svc.is_enabled("feat", tenant_id="t1", workspace_id="w1") is False
        assert svc.is_enabled("feat", tenant_id="t2") is False

    def test_rollout_percentage_is_deterministic(self):
        svc = FeatureFlagService()
        svc.create_flag("ramp", enabled=False, rollout_percentage=50.0)
        first = svc.is_enabled("ramp", subject_key="user-123")
        second = svc.is_enabled("ramp", subject_key="user-123")
        assert first == second  # stable per subject

    def test_rollout_distribution_roughly_matches(self):
        svc = FeatureFlagService()
        svc.create_flag("ramp", enabled=False, rollout_percentage=30.0)
        on = sum(
            1 for i in range(2000) if svc.is_enabled("ramp", subject_key=f"u{i}")
        )
        fraction = on / 2000
        assert 0.25 < fraction < 0.35

    def test_invalid_rollout_rejected(self):
        svc = FeatureFlagService()
        with pytest.raises(FeatureFlagError):
            svc.create_flag("x", rollout_percentage=150.0)

    def test_unknown_flag_is_false(self):
        svc = FeatureFlagService()
        assert svc.is_enabled("nope") is False


# --------------------------------------------------------------------------- #
# Policy evaluation
# --------------------------------------------------------------------------- #
class TestPolicyEngine:
    def test_access_allow_deny(self, audit):
        engine = PolicyEngine(audit=audit)
        engine.register(
            AccessPolicy(
                name="readers",
                rules=[
                    {"match": {"action": "read"}, "effect": "allow"},
                    {"match": {"action": "delete"}, "effect": "deny"},
                ],
            )
        )
        allow = engine.evaluate_access({"action": "read", "resource": "doc"})
        assert allow.allowed is True
        deny = engine.evaluate_access({"action": "delete", "resource": "doc"})
        assert deny.allowed is False

    def test_secure_default_deny_when_no_policy(self):
        engine = PolicyEngine()
        decision = engine.evaluate_access({"action": "read"})
        assert decision.allowed is False
        assert decision.matched_policy_id is None

    def test_condition_when_clause(self):
        engine = PolicyEngine()
        engine.register(
            AccessPolicy(
                name="classified",
                rules=[
                    {
                        "match": {"action": "read"},
                        "when": {"attr": "classification", "op": "in", "value": ["public"]},
                        "effect": "allow",
                    }
                ],
                default_effect=PolicyEffect.DENY,
            )
        )
        assert engine.evaluate_access(
            {"action": "read", "classification": "public"}
        ).allowed
        assert not engine.evaluate_access(
            {"action": "read", "classification": "secret"}
        ).allowed

    def test_priority_ordering(self):
        engine = PolicyEngine()
        engine.register(
            AccessPolicy(
                name="low-pri-allow",
                priority=200,
                rules=[{"match": {"action": "read"}, "effect": "allow"}],
            )
        )
        engine.register(
            AccessPolicy(
                name="high-pri-deny",
                priority=10,
                rules=[{"match": {"action": "read"}, "effect": "deny"}],
            )
        )
        # higher priority (lower number) wins
        assert engine.evaluate_access({"action": "read"}).allowed is False

    def test_retention_specificity(self):
        engine = PolicyEngine()
        engine.register(RetentionPolicy(name="global", retention_days=30))
        engine.register(
            RetentionPolicy(name="tenant", retention_days=90, tenant_id="t1")
        )
        resolved = engine.resolve_retention("docs", tenant_id="t1")
        assert resolved.retention_days == 90

    def test_unregister_missing_raises(self):
        engine = PolicyEngine()
        with pytest.raises(PolicyNotFoundError):
            engine.unregister("nope")


# --------------------------------------------------------------------------- #
# Quota enforcement
# --------------------------------------------------------------------------- #
class TestQuotas:
    def test_enforce_and_block(self, audit):
        svc = QuotaService(audit=audit)
        svc.define_quota(
            tenant_id="t1",
            scope=QuotaScope.REQUEST,
            period=QuotaPeriod.MINUTE,
            limit=3,
        )
        for _ in range(3):
            svc.check_and_consume(
                tenant_id="t1", scope=QuotaScope.REQUEST, period=QuotaPeriod.MINUTE
            )
        with pytest.raises(QuotaExceededError) as exc:
            svc.check_and_consume(
                tenant_id="t1", scope=QuotaScope.REQUEST, period=QuotaPeriod.MINUTE
            )
        assert exc.value.limit == 3
        assert exc.value.current == 3

    def test_shadow_mode_does_not_block(self):
        svc = QuotaService()
        svc.define_quota(
            tenant_id="t1",
            scope=QuotaScope.STORAGE,
            period=QuotaPeriod.TOTAL,
            limit=1,
            enforced=False,
        )
        # would exceed but not enforced
        svc.check_and_consume(
            tenant_id="t1", scope=QuotaScope.STORAGE, period=QuotaPeriod.TOTAL, amount=5
        )
        usage = svc.current_usage(
            tenant_id="t1", scope=QuotaScope.STORAGE, period=QuotaPeriod.TOTAL
        )
        assert usage == 5

    def test_undefined_quota_is_unlimited(self):
        svc = QuotaService()
        usage = svc.check_and_consume(
            tenant_id="t9", scope=QuotaScope.GENERATION, period=QuotaPeriod.DAY, amount=99
        )
        assert usage.current == 99

    def test_window_reset(self):
        svc = QuotaService()
        q = svc.define_quota(
            tenant_id="t1",
            scope=QuotaScope.REQUEST,
            period=QuotaPeriod.SECOND,
            limit=1,
        )
        svc.check_and_consume(
            tenant_id="t1", scope=QuotaScope.REQUEST, period=QuotaPeriod.SECOND
        )
        # force the window to look expired by rewinding window_start
        import datetime as dt
        from backend.admin.models import utcnow

        svc._usage[q.key].window_start = utcnow() - dt.timedelta(seconds=5)
        # next consume should reset and succeed
        svc.check_and_consume(
            tenant_id="t1", scope=QuotaScope.REQUEST, period=QuotaPeriod.SECOND
        )

    def test_usage_report(self):
        svc = QuotaService()
        svc.define_quota(
            tenant_id="t1", scope=QuotaScope.RETRIEVAL, period=QuotaPeriod.DAY, limit=10
        )
        svc.check_and_consume(
            tenant_id="t1", scope=QuotaScope.RETRIEVAL, period=QuotaPeriod.DAY, amount=4
        )
        report = svc.usage_report(tenant_id="t1")
        assert report[0]["current"] == 4
        assert report[0]["utilization"] == pytest.approx(0.4)


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #
class TestGovernance:
    def _pii_rule(self) -> GovernanceRule:
        return GovernanceRule(
            name="encryption_required_for_pii",
            description="PII workspaces must enable encryption",
            severity=GovernanceSeverity.CRITICAL.value,
            remediation="Set settings.encryption_enabled = true",
            condition={
                "all": [
                    {"attr": "settings.classification", "op": "eq", "value": "pii"},
                    {"attr": "settings.encryption_enabled", "op": "ne", "value": True},
                ]
            },
        )

    def test_validate_detects_violation(self, audit):
        gov = GovernanceService(audit=audit)
        gov.register_rule(self._pii_rule())
        violations = gov.validate(
            {"type": "workspace", "settings": {"classification": "pii", "encryption_enabled": False}}
        )
        assert len(violations) == 1
        assert violations[0]["severity"] == "critical"

    def test_validate_passes_compliant_subject(self):
        gov = GovernanceService()
        gov.register_rule(self._pii_rule())
        violations = gov.validate(
            {"settings": {"classification": "pii", "encryption_enabled": True}}
        )
        assert violations == []

    def test_enforce_blocks_critical(self):
        gov = GovernanceService()
        gov.register_rule(self._pii_rule())
        with pytest.raises(GovernanceViolationError):
            gov.enforce(
                {"type": "workspace", "settings": {"classification": "pii", "encryption_enabled": False}}
            )

    def test_warning_does_not_block(self):
        gov = GovernanceService()
        gov.register_rule(
            GovernanceRule(
                name="naming",
                severity=GovernanceSeverity.WARNING.value,
                condition={"attr": "name", "op": "eq", "value": "bad"},
            )
        )
        # should not raise
        gov.enforce({"type": "workspace", "name": "bad"})

    def test_compliance_report(self):
        gov = GovernanceService()
        gov.register_rule(self._pii_rule())
        report = gov.compliance_report(
            [
                {"type": "workspace", "workspace_id": "w1", "settings": {"classification": "pii", "encryption_enabled": True}},
                {"type": "workspace", "workspace_id": "w2", "settings": {"classification": "pii", "encryption_enabled": False}},
            ]
        )
        assert report["total_subjects"] == 2
        assert report["compliant_subjects"] == 1
        assert report["critical_violations"] == 1


# --------------------------------------------------------------------------- #
# Audit logging
# --------------------------------------------------------------------------- #
class TestAudit:
    def test_chain_integrity(self):
        audit = AuditService()
        for i in range(5):
            audit.record(
                category=AuditCategory.ADMIN_ACTION,
                action="test.do",
                actor_id="admin",
                target_id=f"x{i}",
            )
        assert audit.count() == 5
        assert audit.verify_chain() is True

    def test_tamper_detection(self):
        audit = AuditService()
        audit.record(category=AuditCategory.ADMIN_ACTION, action="a", actor_id="u")
        audit.record(category=AuditCategory.ADMIN_ACTION, action="b", actor_id="u")
        # tamper with a stored event
        events = audit.all_events()
        events[0].action = "mutated"
        assert audit.verify_chain() is False

    def test_query_filters(self):
        audit = AuditService()
        audit.record(category=AuditCategory.TENANT, action="tenant.create", actor_id="u1", tenant_id="t1")
        audit.record(category=AuditCategory.POLICY, action="policy.register", actor_id="u2")
        tenant_events = audit.query(category=AuditCategory.TENANT)
        assert len(tenant_events) == 1
        assert tenant_events[0].tenant_id == "t1"


# --------------------------------------------------------------------------- #
# Tenant administration
# --------------------------------------------------------------------------- #
class TestTenantAdmin:
    def test_lifecycle(self, audit):
        cfg = ConfigurationService(audit=audit)
        svc = TenantAdminService(configuration=cfg, audit=audit)
        t = svc.create_tenant("Acme", tenant_id="t1")
        assert t.state == TenantState.ACTIVE
        svc.suspend_tenant("t1")
        assert svc.get_tenant("t1").state == TenantState.SUSPENDED
        svc.reactivate_tenant("t1")
        assert svc.get_tenant("t1").state == TenantState.ACTIVE
        svc.delete_tenant("t1")
        assert svc.get_tenant("t1").state == TenantState.DELETED

    def test_illegal_transition(self):
        svc = TenantAdminService()
        svc.create_tenant("Acme", tenant_id="t1")
        svc.delete_tenant("t1")
        with pytest.raises(TenantStateError):
            svc.suspend_tenant("t1")

    def test_config_created_with_tenant(self):
        cfg = ConfigurationService()
        svc = TenantAdminService(configuration=cfg)
        svc.create_tenant("Acme", tenant_id="t1", config_settings={"plan": "pro"})
        loaded = cfg.load_tenant_config("t1")
        assert loaded.settings["plan"] == "pro"


# --------------------------------------------------------------------------- #
# Workspace administration
# --------------------------------------------------------------------------- #
class TestWorkspaceAdmin:
    def _wire(self):
        audit = AuditService()
        cfg = ConfigurationService(audit=audit)
        tenants = TenantAdminService(configuration=cfg, audit=audit)
        gov = GovernanceService(audit=audit)
        ws = WorkspaceAdminService(
            tenant_admin=tenants, configuration=cfg, governance=gov, audit=audit
        )
        tenants.create_tenant("Acme", tenant_id="t1")
        return tenants, cfg, gov, ws

    def test_create_archive_delete(self):
        _, _, _, ws = self._wire()
        w = ws.create_workspace("t1", "Engineering", workspace_id="w1")
        assert w.state == WorkspaceState.ACTIVE
        ws.archive_workspace("w1")
        assert ws.get_workspace("w1").state == WorkspaceState.ARCHIVED
        ws.unarchive_workspace("w1")
        assert ws.get_workspace("w1").state == WorkspaceState.ACTIVE
        ws.delete_workspace("w1")
        assert ws.get_workspace("w1").state == WorkspaceState.DELETED

    def test_governance_blocks_creation(self):
        _, _, gov, ws = self._wire()
        gov.register_rule(
            GovernanceRule(
                name="pii_encryption",
                severity=GovernanceSeverity.CRITICAL.value,
                condition={
                    "all": [
                        {"attr": "settings.classification", "op": "eq", "value": "pii"},
                        {"attr": "settings.encryption_enabled", "op": "ne", "value": True},
                    ]
                },
            )
        )
        with pytest.raises(GovernanceViolationError):
            ws.create_workspace(
                "t1", "Bad", config_settings={"classification": "pii", "encryption_enabled": False}
            )

    def test_illegal_workspace_transition(self):
        _, _, _, ws = self._wire()
        ws.create_workspace("t1", "Eng", workspace_id="w1")
        ws.delete_workspace("w1")
        with pytest.raises(WorkspaceStateError):
            ws.archive_workspace("w1")


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
class TestHealth:
    def test_all_healthy(self):
        svc = HealthService()
        svc.register_component(
            "config", lambda: ComponentCheck("config", HealthStatus.HEALTHY)
        )
        svc.register_dependency(
            "db", lambda: ComponentCheck("db", HealthStatus.HEALTHY)
        )
        health = svc.check_health()
        assert health.status == HealthStatus.HEALTHY

    def test_unhealthy_dependency_dominates(self):
        svc = HealthService()
        svc.register_component(
            "config", lambda: ComponentCheck("config", HealthStatus.HEALTHY)
        )
        svc.register_dependency(
            "db", lambda: ComponentCheck("db", HealthStatus.UNHEALTHY)
        )
        assert svc.check_health().status == HealthStatus.UNHEALTHY

    def test_degraded(self):
        svc = HealthService()
        svc.register_component(
            "cache", lambda: ComponentCheck("cache", HealthStatus.DEGRADED)
        )
        assert svc.check_health().status == HealthStatus.DEGRADED

    def test_readiness_reflects_critical_deps(self):
        svc = HealthService()
        svc.register_dependency(
            "db", lambda: ComponentCheck("db", HealthStatus.UNHEALTHY), critical=True
        )
        readiness = svc.readiness()
        assert readiness["ready"] is False
        assert "db" in readiness["not_ready"]

    def test_throwing_check_is_unhealthy(self):
        svc = HealthService()

        def boom() -> ComponentCheck:
            raise RuntimeError("down")

        svc.register_dependency("queue", boom)
        assert svc.check_health().status == HealthStatus.UNHEALTHY

    def test_liveness_always_alive(self):
        svc = HealthService()
        assert svc.liveness()["status"] == HealthStatus.HEALTHY.value


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
class TestMetrics:
    def test_quota_violation_metric(self, metrics):
        svc = QuotaService()
        svc.define_quota(
            tenant_id="t1", scope=QuotaScope.REQUEST, period=QuotaPeriod.MINUTE, limit=1
        )
        svc.check_and_consume(
            tenant_id="t1", scope=QuotaScope.REQUEST, period=QuotaPeriod.MINUTE
        )
        with pytest.raises(QuotaExceededError):
            svc.check_and_consume(
                tenant_id="t1", scope=QuotaScope.REQUEST, period=QuotaPeriod.MINUTE
            )
        assert metrics.counter_value(
            QUOTA_VIOLATIONS, scope="request", period="minute"
        ) == 1

    def test_policy_evaluation_metric(self, metrics):
        engine = PolicyEngine()
        engine.register(
            AccessPolicy(name="p", rules=[{"match": {"action": "read"}, "effect": "allow"}])
        )
        engine.evaluate_access({"action": "read"})
        assert metrics.counter_value(POLICY_EVALUATIONS, type="access", effect="allow") == 1

    def test_governance_violation_metric(self, metrics):
        gov = GovernanceService()
        gov.register_rule(
            GovernanceRule(
                name="r",
                severity=GovernanceSeverity.WARNING.value,
                condition={"attr": "bad", "op": "eq", "value": True},
            )
        )
        gov.validate({"bad": True})
        assert metrics.counter_value(GOVERNANCE_VIOLATIONS, severity="warning") == 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
