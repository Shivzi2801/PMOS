"""
versioning.py
=============

Owns the API version contract for PMOS.

Why this file exists
--------------------
External clients (a future UI, partner integrations, enterprise customers)
must be able to depend on a stable contract. The moment we ship ``/api/v1``
we are making a backwards-compatibility promise. This module centralizes the
version constants and the policy for how new major versions are introduced so
that no other file hard-codes a version string.

Versioning strategy
--------------------
* **URL-path major versioning** (``/api/v1``, ``/api/v2``). A major version
  only changes for breaking changes. Clients that hard-code ``/api/v1`` keep
  working forever, even after ``/api/v2`` ships, because both routers are
  mounted side by side.
* **Semantic version reported at runtime** via ``GET /api/v1/version`` so a
  client can detect additive (non-breaking) changes within a major version.

Adding a v2 in the future
-------------------------
1. Add ``"v2"`` to ``SUPPORTED_VERSIONS``.
2. Build a ``router_v2`` (typically importing/extending v1 route handlers).
3. Mount it in ``app.py`` at ``/api/v2``.
No existing v1 client breaks because v1 routes are never removed in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

# Semantic version of the API *contract* (not the app/build version).
API_VERSION = "1.0.0"

# The current default major-version path segment.
API_VERSION_TAG = "v1"

# Every major version the server currently serves. New entries are added,
# old entries are never removed while clients depend on them.
SUPPORTED_VERSIONS: List[str] = ["v1"]


@dataclass(frozen=True)
class VersionInfo:
    """Immutable description of the running API version contract."""

    api_version: str
    current_major: str
    supported_majors: List[str]
    deprecated_majors: List[str]

    def as_dict(self) -> Dict[str, object]:
        return {
            "api_version": self.api_version,
            "current_major": self.current_major,
            "supported_majors": self.supported_majors,
            "deprecated_majors": self.deprecated_majors,
        }


def get_version_info() -> VersionInfo:
    """Return the current version contract as a structured object."""
    return VersionInfo(
        api_version=API_VERSION,
        current_major=API_VERSION_TAG,
        supported_majors=list(SUPPORTED_VERSIONS),
        deprecated_majors=[],
    )


def version_prefix(major: str = API_VERSION_TAG) -> str:
    """Return the URL prefix for a given major version, e.g. ``/api/v1``."""
    return f"/api/{major}"
