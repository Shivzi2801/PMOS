"""
health.py
=========

Operational endpoints: health, version, and metrics.

Why this file exists
--------------------
Operators, load balancers, and uptime monitors need a cheap, dependency-aware
way to ask "is the API up, and are its downstream modules reachable?" These
endpoints are intentionally kept separate from business routes: they are not
rate limited, not authenticated, and must stay fast and side-effect free.

* ``GET /health``  — liveness + dependency readiness (the nine backend modules).
* ``GET /version`` — the API version contract (drives client compatibility).
* ``GET /metrics`` — the observability snapshot from ``api_metrics``.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from .api_metrics import MetricsRegistry, get_metrics_registry
from .response_models import (
    HealthData,
    MetricsData,
    ResponseEnvelope,
    VersionData,
)
from .services import PMOSServices, get_services
from .versioning import get_version_info

router = APIRouter(tags=["operations"])

_START_TIME = time.time()


@router.get("/health", response_model=ResponseEnvelope)
def health(request: Request, services: PMOSServices = Depends(get_services)):
    """Report liveness and the connectivity of each backend module."""
    deps = services.dependency_status()
    overall = "ok" if all(v == "connected" for v in deps.values()) else "degraded"
    data = HealthData(
        status=overall,
        dependencies=deps,
        uptime_seconds=round(time.time() - _START_TIME, 3),
    )
    return ResponseEnvelope.ok(request.state.request_id, data.model_dump())


@router.get("/version", response_model=ResponseEnvelope)
def version(request: Request):
    """Return the API version contract for client compatibility checks."""
    info = get_version_info().as_dict()
    data = VersionData(**info)
    return ResponseEnvelope.ok(request.state.request_id, data.model_dump())


@router.get("/metrics", response_model=ResponseEnvelope)
def metrics(
    request: Request,
    registry: MetricsRegistry = Depends(get_metrics_registry),
):
    """Return the current observability snapshot."""
    snap = registry.snapshot()
    data = MetricsData(**snap)
    return ResponseEnvelope.ok(request.state.request_id, data.model_dump())
