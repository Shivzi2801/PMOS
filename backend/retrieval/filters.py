"""
backend/retrieval/filters.py

Post-retrieval filtering primitives (S1.6).

Three concerns live here, applied in a fixed, security-first order:

1. TenantIsolationFilter  (responsibility #5) -- hard invariant.
2. AclFilter              (responsibility #4) -- silent drop of inaccessible.
3. MetadataFilter         (responsibility #2) -- evaluates the query's
   ``FilterClause`` against each hit's metadata.

Ordering matters: tenant isolation runs first and *raises* on any foreign-tenant
record (defence in depth -- the store should already scope by tenant, but we
never trust a single layer for isolation). ACL and metadata filtering then
*drop* records silently, because "you can't see it" and "it didn't match your
filter" are both ordinary, non-error outcomes.

ACL model
---------
Each hit's metadata may carry an ``acl`` entry describing who may read it:

    metadata["acl"] = {
        "allow": ["user:alice", "group:finance", "*"],   # principals or "*"
        "deny":  ["user:bob"],                            # optional explicit deny
    }

A hit is visible iff:
    * not denied for any requesting principal, AND
    * ("*" in allow) OR (allow ∩ principals is non-empty)

A missing/empty ``acl`` is treated as *private to the tenant* (allow = []),
which is the safe default: nothing is world-readable unless explicitly marked.

No external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable, List, Mapping, Sequence, Tuple

from .errors import TenantIsolationError
from .retrieval_query import FilterClause
from .retrieval_result import RetrievalHit

WILDCARD = "*"


@dataclass(frozen=True)
class FilterStats:
    """How many hits survived each stage (fed into diagnostics/metrics)."""

    after_tenant: int
    after_acl: int
    after_metadata: int


def enforce_tenant_isolation(
    hits: Sequence[RetrievalHit], tenant_id: str
) -> List[RetrievalHit]:
    """
    Hard tenant-isolation gate.

    Any hit whose ``tenant_id`` does not equal the query tenant is a leak and
    raises ``TenantIsolationError`` immediately. This is intentionally
    fail-closed: we do not silently drop, because a foreign-tenant record in
    the candidate set means an upstream scoping bug that must surface loudly.
    """
    if not tenant_id:
        raise TenantIsolationError("empty tenant_id during isolation enforcement")
    for hit in hits:
        if hit.tenant_id != tenant_id:
            raise TenantIsolationError(
                "cross-tenant record encountered in candidate set",
                details={
                    "expected_tenant": tenant_id,
                    "found_tenant": hit.tenant_id,
                    "chunk_id": hit.chunk_id,
                },
            )
    return list(hits)


def _acl_visible(metadata: Mapping[str, Any], principals: FrozenSet[str]) -> bool:
    acl = metadata.get("acl") or {}
    allow = set(acl.get("allow", ()) or ())
    deny = set(acl.get("deny", ()) or ())

    # Explicit deny wins.
    if deny & principals:
        return False
    if WILDCARD in allow:
        return True
    return bool(allow & principals)


def apply_acl(
    hits: Sequence[RetrievalHit], principals: FrozenSet[str]
) -> List[RetrievalHit]:
    """
    Silently drop hits the requesting principals cannot read.

    Returns a new list; never raises for ordinary access denials.
    """
    return [h for h in hits if _acl_visible(h.metadata, principals)]


def apply_metadata_filters(
    hits: Sequence[RetrievalHit], clause: FilterClause
) -> List[RetrievalHit]:
    """Keep only hits whose metadata satisfies the query's filter clause."""
    return [h for h in hits if clause.matches(h.metadata)]


def apply_all(
    hits: Sequence[RetrievalHit],
    *,
    tenant_id: str,
    principals: FrozenSet[str],
    clause: FilterClause,
) -> Tuple[List[RetrievalHit], FilterStats]:
    """
    Run the full security-first filter pipeline and report per-stage counts.

    Order: tenant isolation (raises) -> ACL (drops) -> metadata (drops).
    """
    tenant_safe = enforce_tenant_isolation(hits, tenant_id)
    after_tenant = len(tenant_safe)

    acl_safe = apply_acl(tenant_safe, principals)
    after_acl = len(acl_safe)

    filtered = apply_metadata_filters(acl_safe, clause)
    after_meta = len(filtered)

    return filtered, FilterStats(
        after_tenant=after_tenant,
        after_acl=after_acl,
        after_metadata=after_meta,
    )
