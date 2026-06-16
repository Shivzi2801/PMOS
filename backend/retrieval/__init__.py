"""
backend/retrieval — Retrieval Layer (Wave 1, Slice 1.6).

Public API for downstream RAG systems and API layers. Import from the package
root rather than submodules to stay decoupled from internal layout.
"""

from __future__ import annotations

from .errors import (
    AclDeniedError,
    IndexUnavailableError,
    InvalidQueryError,
    PaginationError,
    QueryExpansionError,
    RerankError,
    RetrievalError,
    TenantIsolationError,
)
from .filters import apply_acl, apply_all, apply_metadata_filters, enforce_tenant_isolation
from .hybrid_retriever import HybridRetriever, KeywordIndex
from .metrics import (
    InMemoryMetrics,
    MetricsSink,
    Names as MetricNames,
    NullMetrics,
    RetrievalEvent,
)
from .query_expansion import (
    CompositeExpander,
    ExpansionResult,
    NoopExpander,
    QueryExpander,
    SynonymExpander,
    safe_expand,
)
from .reranker import (
    IdentityReranker,
    LexicalOverlapReranker,
    Reranker,
    safe_rerank,
)
from .retrieval_query import (
    BoolOp,
    FieldFilter,
    FilterClause,
    FilterOp,
    MATCH_ALL,
    Pagination,
    RetrievalQuery,
)
from .retrieval_result import (
    PageInfo,
    RetrievalDiagnostics,
    RetrievalHit,
    RetrievalResult,
)
from .retriever import IndexCandidate, Retriever, VectorIndex

__all__ = [
    # query
    "RetrievalQuery",
    "FieldFilter",
    "FilterClause",
    "FilterOp",
    "BoolOp",
    "Pagination",
    "MATCH_ALL",
    # result
    "RetrievalResult",
    "RetrievalHit",
    "PageInfo",
    "RetrievalDiagnostics",
    # retrievers + indexing contract
    "Retriever",
    "HybridRetriever",
    "VectorIndex",
    "IndexCandidate",
    "KeywordIndex",
    # rerank
    "Reranker",
    "IdentityReranker",
    "LexicalOverlapReranker",
    "safe_rerank",
    # expansion
    "QueryExpander",
    "NoopExpander",
    "SynonymExpander",
    "CompositeExpander",
    "ExpansionResult",
    "safe_expand",
    # filters
    "enforce_tenant_isolation",
    "apply_acl",
    "apply_metadata_filters",
    "apply_all",
    # metrics
    "MetricsSink",
    "NullMetrics",
    "InMemoryMetrics",
    "RetrievalEvent",
    "MetricNames",
    # errors
    "RetrievalError",
    "InvalidQueryError",
    "TenantIsolationError",
    "AclDeniedError",
    "IndexUnavailableError",
    "RerankError",
    "QueryExpansionError",
    "PaginationError",
]
