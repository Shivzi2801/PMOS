"""
PMOS API Layer (Slice S2.1)
===========================

This package is the single HTTP entry point into the Product Management
Operating System (PMOS). It exposes the capabilities implemented by the
backend service modules (connectors, ingestion, extraction, resolution,
indexing, retrieval, context, generation, grounding) over a versioned,
standardized REST interface.

Public surface
--------------
- ``create_app``      : application factory (see ``app.py``)
- ``API_VERSION``     : current semantic version of the API contract
- ``API_VERSION_TAG`` : URL path segment for the current major version

The factory pattern is used so that tests, ASGI servers, and future
embedding contexts can each construct an isolated application instance with
their own configuration instead of relying on a shared global object.
"""

from .versioning import API_VERSION, API_VERSION_TAG, SUPPORTED_VERSIONS
from .app import create_app

__all__ = [
    "create_app",
    "API_VERSION",
    "API_VERSION_TAG",
    "SUPPORTED_VERSIONS",
]
