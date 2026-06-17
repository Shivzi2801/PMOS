"""
app.py
======

The application factory. Assembles middleware, error handlers, the versioned
business router, and the operational endpoints into a single FastAPI app.

Why this file exists
--------------------
One place must compose the whole API: register cross-cutting middleware, attach
the centralized error handlers, and mount routers under their version prefix.
The factory pattern (``create_app``) lets tests and servers build isolated
instances and makes future multi-version mounting explicit.

Request lifecycle (assembled here)
----------------------------------
client -> CorrelationId -> RateLimit -> TimingMetrics -> route handler
       -> service adapter -> backend module -> envelope -> (errors centralized)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .error_handlers import register_error_handlers
from .health import router as ops_router
from .middleware import register_middleware
from .router import router as v1_router
from .versioning import API_VERSION, SUPPORTED_VERSIONS, version_prefix


def _configure_logging() -> None:
    # Idempotent basic config; real deployments inject their own handlers.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )


def create_app() -> FastAPI:
    """Construct and return a fully wired PMOS API application."""
    _configure_logging()

    app = FastAPI(
        title="PMOS API",
        version=API_VERSION,
        description="Product Management Operating System — REST API (Slice S2.1).",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Cross-cutting middleware (correlation id, rate limit, timing/metrics).
    register_middleware(app)

    # Centralized error -> envelope translation.
    register_error_handlers(app)

    # Mount business + operational routers for every supported major version.
    # Mounting both under the version prefix means adding /api/v2 later is
    # purely additive and never disturbs existing v1 clients.
    for major in SUPPORTED_VERSIONS:
        prefix = version_prefix(major)
        app.include_router(v1_router, prefix=prefix)
        app.include_router(ops_router, prefix=prefix)

    @app.get("/", tags=["operations"])
    def root():
        return {
            "service": "PMOS API",
            "version": API_VERSION,
            "supported_versions": SUPPORTED_VERSIONS,
            "docs": "/docs",
        }

    return app


# Module-level app for ASGI servers: ``uvicorn backend.api.app:app``.
app = create_app()
