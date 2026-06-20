"""Feature flag service: lifecycle and evaluation of feature flags."""
from __future__ import annotations

import threading
from typing import Optional

from .audit_service import AuditService
from .errors import FeatureFlagError, FeatureFlagNotFoundError
from .metrics import FEATURE_FLAG_EVALUATIONS, get_metrics
from .models import AuditCategory, FeatureFlag, utcnow


class FeatureFlagService:
    """Create, mutate and evaluate feature flags.

    Flags are addressed by their unique ``key``. Evaluation is delegated to the
    flag model's resolution logic (overrides -> rollout -> default); the service
    layers in persistence, validation, audit and metrics.
    """

    def __init__(self, *, audit: Optional[AuditService] = None) -> None:
        self._lock = threading.RLock()
        self._flags: dict[str, FeatureFlag] = {}
        self._audit = audit

    def create_flag(
        self,
        key: str,
        *,
        description: str = "",
        enabled: bool = False,
        rollout_percentage: float = 0.0,
        actor_id: str = "system",
    ) -> FeatureFlag:
        if not key or not key.strip():
            raise FeatureFlagError("flag key must be non-empty")
        if not 0.0 <= rollout_percentage <= 100.0:
            raise FeatureFlagError(
                "rollout_percentage must be within [0, 100]",
                details={"rollout_percentage": rollout_percentage},
            )
        with self._lock:
            if key in self._flags:
                raise FeatureFlagError(
                    f"feature flag '{key}' already exists", details={"key": key}
                )
            flag = FeatureFlag(
                key=key,
                description=description,
                enabled=enabled,
                rollout_percentage=rollout_percentage,
                updated_by=actor_id,
            )
            self._flags[key] = flag
        self._audit_change("feature_flag.create", flag, actor_id)
        return flag

    def get_flag(self, key: str) -> FeatureFlag:
        with self._lock:
            flag = self._flags.get(key)
            if flag is None:
                raise FeatureFlagNotFoundError(
                    f"feature flag '{key}' not found", details={"key": key}
                )
            return flag

    def list_flags(self) -> list[FeatureFlag]:
        with self._lock:
            return list(self._flags.values())

    def enable(self, key: str, *, actor_id: str = "system") -> FeatureFlag:
        return self._set_enabled(key, True, actor_id)

    def disable(self, key: str, *, actor_id: str = "system") -> FeatureFlag:
        return self._set_enabled(key, False, actor_id)

    def set_rollout(
        self, key: str, percentage: float, *, actor_id: str = "system"
    ) -> FeatureFlag:
        if not 0.0 <= percentage <= 100.0:
            raise FeatureFlagError(
                "rollout_percentage must be within [0, 100]",
                details={"percentage": percentage},
            )
        with self._lock:
            flag = self.get_flag(key)
            flag.rollout_percentage = percentage
            flag.updated_at = utcnow()
            flag.updated_by = actor_id
        self._audit_change("feature_flag.set_rollout", flag, actor_id)
        return flag

    def set_tenant_override(
        self, key: str, tenant_id: str, value: bool, *, actor_id: str = "system"
    ) -> FeatureFlag:
        with self._lock:
            flag = self.get_flag(key)
            flag.tenant_overrides[tenant_id] = value
            flag.updated_at = utcnow()
            flag.updated_by = actor_id
        self._audit_change("feature_flag.tenant_override", flag, actor_id)
        return flag

    def clear_tenant_override(
        self, key: str, tenant_id: str, *, actor_id: str = "system"
    ) -> FeatureFlag:
        with self._lock:
            flag = self.get_flag(key)
            flag.tenant_overrides.pop(tenant_id, None)
            flag.updated_at = utcnow()
            flag.updated_by = actor_id
        self._audit_change("feature_flag.clear_tenant_override", flag, actor_id)
        return flag

    def set_workspace_override(
        self, key: str, workspace_id: str, value: bool, *, actor_id: str = "system"
    ) -> FeatureFlag:
        with self._lock:
            flag = self.get_flag(key)
            flag.workspace_overrides[workspace_id] = value
            flag.updated_at = utcnow()
            flag.updated_by = actor_id
        self._audit_change("feature_flag.workspace_override", flag, actor_id)
        return flag

    def clear_workspace_override(
        self, key: str, workspace_id: str, *, actor_id: str = "system"
    ) -> FeatureFlag:
        with self._lock:
            flag = self.get_flag(key)
            flag.workspace_overrides.pop(workspace_id, None)
            flag.updated_at = utcnow()
            flag.updated_by = actor_id
        self._audit_change("feature_flag.clear_workspace_override", flag, actor_id)
        return flag

    def is_enabled(
        self,
        key: str,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_key: Optional[str] = None,
    ) -> bool:
        """Evaluate a flag for a context. Unknown flags resolve to False."""
        with self._lock:
            flag = self._flags.get(key)
        if flag is None:
            get_metrics().increment(FEATURE_FLAG_EVALUATIONS, key=key, result="missing")
            return False
        result = flag.evaluate(
            tenant_id=tenant_id, workspace_id=workspace_id, subject_key=subject_key
        )
        get_metrics().increment(
            FEATURE_FLAG_EVALUATIONS, key=key, result="on" if result else "off"
        )
        return result

    def delete_flag(self, key: str, *, actor_id: str = "system") -> None:
        with self._lock:
            flag = self._flags.pop(key, None)
        if flag is None:
            raise FeatureFlagNotFoundError(
                f"feature flag '{key}' not found", details={"key": key}
            )
        self._audit_change("feature_flag.delete", flag, actor_id)

    # internals ------------------------------------------------------------ #
    def _set_enabled(self, key: str, value: bool, actor_id: str) -> FeatureFlag:
        with self._lock:
            flag = self.get_flag(key)
            flag.enabled = value
            flag.updated_at = utcnow()
            flag.updated_by = actor_id
        self._audit_change(
            f"feature_flag.{'enable' if value else 'disable'}", flag, actor_id
        )
        return flag

    def _audit_change(self, action: str, flag: FeatureFlag, actor_id: str) -> None:
        if self._audit is None:
            return
        self._audit.record(
            category=AuditCategory.CONFIGURATION,
            action=action,
            actor_id=actor_id,
            target_type="feature_flag",
            target_id=flag.key,
            after=flag.to_dict(),
        )
