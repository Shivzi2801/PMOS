"""
backend/retrieval/retrieval_query.py

Query contract for the Retrieval Layer (S1.6).

A ``RetrievalQuery`` is the single input object accepted by every retriever.
It is intentionally transport-agnostic: an API layer, a RAG orchestrator, or a
test harness all construct the same dataclass.

Design notes
------------
* Frozen dataclasses for value-object semantics and safe hashing/caching.
* Validation happens in ``__post_init__`` so an invalid query can never be
  silently executed.
* ``tenant_id`` is mandatory and is the cornerstone of tenant isolation
  (responsibility #5). There is deliberately no "default tenant".
* ``acl_principals`` carries the set of identities (user + groups/roles) used
  for source-ACL filtering (responsibility #4).
* Metadata filters (responsibility #2) are expressed with a small, explicit
  predicate tree rather than free-form dicts, so the contract with the
  Indexing Layer is unambiguous and testable.

No external dependencies.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any, FrozenSet, Mapping, Optional, Sequence, Tuple

from .errors import InvalidQueryError, PaginationError

# Defaults / guard-rails ------------------------------------------------------
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 200
MAX_OFFSET = 10_000  # deep-pagination guard; downstream should use cursors


class FilterOp(enum.Enum):
    """Supported metadata filter operators."""

    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EXISTS = "exists"
    CONTAINS = "contains"  # substring / membership against a sequence field


class BoolOp(enum.Enum):
    """Boolean combinators for composing filter clauses."""

    AND = "and"
    OR = "or"
    NOT = "not"


@dataclass(frozen=True)
class FieldFilter:
    """
    A single predicate over one metadata field.

    Examples
    --------
    FieldFilter("source", FilterOp.EQ, "confluence")
    FieldFilter("created_at", FilterOp.GTE, 1700000000)
    FieldFilter("labels", FilterOp.CONTAINS, "finance")
    FieldFilter("owner", FilterOp.EXISTS, True)
    """

    field: str
    op: FilterOp
    value: Any = None

    def __post_init__(self) -> None:
        if not self.field or not isinstance(self.field, str):
            raise InvalidQueryError(
                "FieldFilter.field must be a non-empty string",
                details={"field": self.field},
            )
        if self.op in (FilterOp.IN, FilterOp.NOT_IN):
            if not isinstance(self.value, (list, tuple, set, frozenset)):
                raise InvalidQueryError(
                    f"Operator {self.op.value} requires a collection value",
                    details={"field": self.field, "op": self.op.value},
                )
            # Normalize to a hashable, order-independent value.
            object.__setattr__(self, "value", frozenset(self.value))

    def matches(self, metadata: Mapping[str, Any]) -> bool:
        """Evaluate this predicate against a document's metadata mapping."""
        present = self.field in metadata
        if self.op is FilterOp.EXISTS:
            return present is bool(self.value)
        if not present:
            # Absent field never matches a value predicate (except NE/NOT_IN,
            # which treat "absent" as "not equal").
            return self.op in (FilterOp.NE, FilterOp.NOT_IN)

        actual = metadata[self.field]
        op = self.op
        try:
            if op is FilterOp.EQ:
                return actual == self.value
            if op is FilterOp.NE:
                return actual != self.value
            if op is FilterOp.IN:
                return actual in self.value
            if op is FilterOp.NOT_IN:
                return actual not in self.value
            if op is FilterOp.GT:
                return actual > self.value
            if op is FilterOp.GTE:
                return actual >= self.value
            if op is FilterOp.LT:
                return actual < self.value
            if op is FilterOp.LTE:
                return actual <= self.value
            if op is FilterOp.CONTAINS:
                return self.value in actual
        except TypeError:
            # Incomparable types -> predicate simply does not match.
            return False
        return False


@dataclass(frozen=True)
class FilterClause:
    """
    A boolean combination of ``FieldFilter`` / nested ``FilterClause`` nodes.

    AND/OR take any number of children; NOT takes exactly one. An empty
    AND clause is the identity (matches everything), which makes "no filter"
    a natural default.
    """

    op: BoolOp
    children: Tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.children, tuple):
            object.__setattr__(self, "children", tuple(self.children))
        if self.op is BoolOp.NOT and len(self.children) != 1:
            raise InvalidQueryError(
                "NOT clause requires exactly one child",
                details={"child_count": len(self.children)},
            )
        for child in self.children:
            if not isinstance(child, (FieldFilter, FilterClause)):
                raise InvalidQueryError(
                    "FilterClause children must be FieldFilter or FilterClause",
                    details={"child_type": type(child).__name__},
                )

    def matches(self, metadata: Mapping[str, Any]) -> bool:
        if self.op is BoolOp.AND:
            return all(c.matches(metadata) for c in self.children)
        if self.op is BoolOp.OR:
            return any(c.matches(metadata) for c in self.children)
        # NOT
        return not self.children[0].matches(metadata)

    # Ergonomic constructors --------------------------------------------------
    @staticmethod
    def all_of(*children: Any) -> "FilterClause":
        return FilterClause(BoolOp.AND, tuple(children))

    @staticmethod
    def any_of(*children: Any) -> "FilterClause":
        return FilterClause(BoolOp.OR, tuple(children))

    @staticmethod
    def negate(child: Any) -> "FilterClause":
        return FilterClause(BoolOp.NOT, (child,))


# Empty AND == match-all. Reused as the default filter.
MATCH_ALL = FilterClause(BoolOp.AND, ())


@dataclass(frozen=True)
class Pagination:
    """Offset/limit pagination with deep-pagination guard-rails."""

    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if not isinstance(self.offset, int) or self.offset < 0:
            raise PaginationError(
                "offset must be a non-negative integer",
                details={"offset": self.offset},
            )
        if not isinstance(self.limit, int) or self.limit <= 0:
            raise PaginationError(
                "limit must be a positive integer",
                details={"limit": self.limit},
            )
        if self.limit > MAX_PAGE_SIZE:
            raise PaginationError(
                f"limit exceeds MAX_PAGE_SIZE ({MAX_PAGE_SIZE})",
                details={"limit": self.limit, "max": MAX_PAGE_SIZE},
            )
        if self.offset > MAX_OFFSET:
            raise PaginationError(
                f"offset exceeds MAX_OFFSET ({MAX_OFFSET}); use cursoring",
                details={"offset": self.offset, "max": MAX_OFFSET},
            )

    @property
    def end(self) -> int:
        return self.offset + self.limit


@dataclass(frozen=True)
class RetrievalQuery:
    """
    The canonical retrieval request.

    Parameters
    ----------
    text:
        Natural-language query text. Required and non-empty.
    tenant_id:
        Mandatory tenant scope. Enforced everywhere downstream.
    acl_principals:
        Identities used for source-ACL filtering. A document is visible if its
        ACL allow-set intersects this set (see ``filters.AclFilter``). An empty
        principal set means "no principals" -> only world-readable docs match.
    filters:
        Metadata predicate tree. Defaults to MATCH_ALL.
    pagination:
        Offset/limit. Defaults to the first page.
    top_k:
        Candidate pool size pulled from the vector store *before* filtering and
        reranking. Must be >= pagination.end so a full page can be filled.
    min_score:
        Optional similarity floor; candidates below it are discarded.
    enable_query_expansion:
        Toggle for query-expansion hooks (responsibility #6).
    enable_rerank:
        Toggle for reranking (responsibility #3).
    metadata:
        Free-form caller annotations (request id, locale, ...). Not used for
        filtering; surfaced in metrics/logs.
    """

    text: str
    tenant_id: str
    acl_principals: FrozenSet[str] = frozenset()
    filters: FilterClause = MATCH_ALL
    pagination: Pagination = field(default_factory=Pagination)
    top_k: int = 50
    min_score: Optional[float] = None
    enable_query_expansion: bool = False
    enable_rerank: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise InvalidQueryError("query text must be a non-empty string")
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise TenantMissing()
        if not isinstance(self.acl_principals, frozenset):
            object.__setattr__(
                self, "acl_principals", frozenset(self.acl_principals)
            )
        if not isinstance(self.filters, FilterClause):
            raise InvalidQueryError(
                "filters must be a FilterClause",
                details={"type": type(self.filters).__name__},
            )
        if not isinstance(self.top_k, int) or self.top_k <= 0:
            raise InvalidQueryError(
                "top_k must be a positive integer", details={"top_k": self.top_k}
            )
        if self.top_k < self.pagination.end:
            # Ensure the candidate pool can satisfy the requested page.
            object.__setattr__(self, "top_k", self.pagination.end)
        if self.min_score is not None and not isinstance(
            self.min_score, (int, float)
        ):
            raise InvalidQueryError(
                "min_score must be numeric or None",
                details={"min_score": self.min_score},
            )

    # Convenience -------------------------------------------------------------
    def with_text(self, text: str) -> "RetrievalQuery":
        """Return a copy with replaced text (used by query expansion)."""
        return replace(self, text=text)

    def with_filters(self, filters: FilterClause) -> "RetrievalQuery":
        return replace(self, filters=filters)

    def fingerprint(self) -> Tuple[Any, ...]:
        """A hashable identity used for cache keys / dedupe in metrics."""
        return (
            self.text,
            self.tenant_id,
            self.acl_principals,
            self.top_k,
            self.min_score,
            self.pagination.offset,
            self.pagination.limit,
        )


def TenantMissing() -> InvalidQueryError:
    """Factory for the canonical missing-tenant error (kept terse for callers)."""
    from .errors import TenantIsolationError

    return TenantIsolationError("tenant_id is required on every RetrievalQuery")
