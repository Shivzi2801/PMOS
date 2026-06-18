"""
Identity metrics.

In-process registry for the identity/tenancy KPIs required by the spec:

    active_users               gauge   (live, non-expired sessions' distinct users)
    active_tenants             gauge   (tenants in ACTIVE status)
    active_workspaces          gauge   (workspaces in ACTIVE status)
    api_key_usage              counter (validations per key)
    permission_denials         counter
    authentication_attempts    counter (by outcome)
    tenant_resolution_latency  histogram (seconds)

Gauges that depend on live state (active users/tenants/workspaces) are computed
on demand from injected providers so they never drift from reality; counters and
the latency histogram accumulate. `snapshot()` returns a JSON-serialisable view
for dashboards / a Prometheus exporter.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from statistics import mean
from typing import Any, Callable, Dict, Optional


class IdentityMetrics:
    def __init__(self, *,
                 active_users_fn: Optional[Callable[[], int]] = None,
                 active_tenants_fn: Optional[Callable[[], int]] = None,
                 active_workspaces_fn: Optional[Callable[[], int]] = None):
        self._lock = threading.RLock()
        self._api_key_usage: Dict[str, int] = defaultdict(int)
        self._permission_denials = 0
        self._auth_attempts: Dict[str, int] = defaultdict(int)  # outcome -> n
        self._resolution_latencies: list = []
        self._active_users_fn = active_users_fn
        self._active_tenants_fn = active_tenants_fn
        self._active_workspaces_fn = active_workspaces_fn

    # --- counters -----------------------------------------------------------
    def incr_api_key_usage(self, key_id: str) -> None:
        with self._lock:
            self._api_key_usage[key_id] += 1

    def incr_permission_denial(self) -> None:
        with self._lock:
            self._permission_denials += 1

    def incr_auth_attempt(self, outcome: str) -> None:
        with self._lock:
            self._auth_attempts[outcome] += 1

    def observe_tenant_resolution(self, latency_s: float) -> None:
        with self._lock:
            self._resolution_latencies.append(latency_s)

    # --- gauges (live) ------------------------------------------------------
    def active_users(self) -> int:
        return self._active_users_fn() if self._active_users_fn else 0

    def active_tenants(self) -> int:
        return self._active_tenants_fn() if self._active_tenants_fn else 0

    def active_workspaces(self) -> int:
        return self._active_workspaces_fn() if self._active_workspaces_fn else 0

    # --- export -------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            lat = list(self._resolution_latencies)
            return {
                "active_users": self.active_users(),
                "active_tenants": self.active_tenants(),
                "active_workspaces": self.active_workspaces(),
                "api_key_usage": dict(self._api_key_usage),
                "api_key_usage_total": sum(self._api_key_usage.values()),
                "permission_denials": self._permission_denials,
                "authentication_attempts": dict(self._auth_attempts),
                "tenant_resolution_latency": {
                    "count": len(lat),
                    "avg_s": round(mean(lat), 6) if lat else 0.0,
                    "max_s": round(max(lat), 6) if lat else 0.0,
                    "min_s": round(min(lat), 6) if lat else 0.0,
                },
            }
