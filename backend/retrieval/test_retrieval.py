"""
backend/retrieval/test_retrieval.py

Unit tests for the Retrieval Layer (S1.6). Pure stdlib ``unittest`` — no
external dependencies. Run with:

    python -m unittest backend.retrieval.test_retrieval -v
    # or
    python -m pytest backend/retrieval/test_retrieval.py
"""

from __future__ import annotations

import unittest
from typing import List, Sequence

from backend.retrieval import (
    BoolOp,
    CompositeExpander,
    FieldFilter,
    FilterClause,
    FilterOp,
    HybridRetriever,
    IndexCandidate,
    IdentityReranker,
    InMemoryMetrics,
    InvalidQueryError,
    LexicalOverlapReranker,
    MATCH_ALL,
    MetricNames,
    Pagination,
    RetrievalQuery,
    Retriever,
    SynonymExpander,
    TenantIsolationError,
    apply_acl,
    enforce_tenant_isolation,
)
from backend.retrieval.errors import IndexUnavailableError
from backend.retrieval.query_expansion import safe_expand
from backend.retrieval.reranker import safe_rerank


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeVectorIndex:
    """In-memory VectorIndex honoring the S1.5 contract for tests."""

    def __init__(self, candidates: Sequence[IndexCandidate]):
        self._candidates = list(candidates)

    def search(self, *, query_text, tenant_id, top_k):
        scoped = [c for c in self._candidates if c.tenant_id == tenant_id]
        scoped.sort(key=lambda c: c.score, reverse=True)
        return scoped[:top_k]


class LeakyVectorIndex:
    """Deliberately returns a foreign-tenant record to test isolation defence."""

    def __init__(self, candidates: Sequence[IndexCandidate]):
        self._candidates = list(candidates)

    def search(self, *, query_text, tenant_id, top_k):
        return self._candidates[:top_k]  # ignores tenant on purpose


class BrokenVectorIndex:
    def search(self, *, query_text, tenant_id, top_k):
        raise RuntimeError("backend down")


class FakeKeywordIndex:
    def __init__(self, ranked):
        self._ranked = list(ranked)

    def search_ranked(self, *, query_text, tenant_id, top_k):
        return self._ranked[:top_k]


def cand(cid, *, tenant="t1", score=0.5, text="hello world", source="src", acl=None, **meta):
    md = dict(meta)
    md["source"] = source  # mirror S1.5: source is also a filterable metadata field
    if acl is not None:
        md["acl"] = acl
    return IndexCandidate(
        chunk_id=cid,
        document_id=f"doc-{cid}",
        source=source,
        text=text,
        tenant_id=tenant,
        score=score,
        metadata=md,
    )


# ---------------------------------------------------------------------------
# Query validation
# ---------------------------------------------------------------------------
class TestQueryValidation(unittest.TestCase):
    def test_empty_text_rejected(self):
        with self.assertRaises(InvalidQueryError):
            RetrievalQuery(text="   ", tenant_id="t1")

    def test_missing_tenant_rejected(self):
        with self.assertRaises(TenantIsolationError):
            RetrievalQuery(text="hi", tenant_id="")

    def test_top_k_raised_to_cover_page(self):
        q = RetrievalQuery(
            text="hi", tenant_id="t1", top_k=5, pagination=Pagination(offset=20, limit=10)
        )
        self.assertGreaterEqual(q.top_k, q.pagination.end)

    def test_bad_pagination(self):
        with self.assertRaises(InvalidQueryError):
            Pagination(offset=-1, limit=10)
        with self.assertRaises(InvalidQueryError):
            Pagination(offset=0, limit=0)

    def test_in_operator_requires_collection(self):
        with self.assertRaises(InvalidQueryError):
            FieldFilter("source", FilterOp.IN, "not-a-list")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
class TestFilters(unittest.TestCase):
    def test_field_filter_ops(self):
        md = {"source": "confluence", "labels": ["a", "b"], "n": 5}
        self.assertTrue(FieldFilter("source", FilterOp.EQ, "confluence").matches(md))
        self.assertTrue(FieldFilter("n", FilterOp.GTE, 5).matches(md))
        self.assertTrue(FieldFilter("labels", FilterOp.CONTAINS, "a").matches(md))
        self.assertTrue(FieldFilter("source", FilterOp.IN, ["confluence", "jira"]).matches(md))
        self.assertFalse(FieldFilter("source", FilterOp.NE, "confluence").matches(md))
        self.assertTrue(FieldFilter("missing", FilterOp.EXISTS, False).matches(md))

    def test_boolean_clauses(self):
        md = {"source": "jira", "n": 3}
        clause = FilterClause.all_of(
            FieldFilter("source", FilterOp.EQ, "jira"),
            FilterClause.any_of(
                FieldFilter("n", FilterOp.GT, 10),
                FieldFilter("n", FilterOp.LT, 5),
            ),
        )
        self.assertTrue(clause.matches(md))
        self.assertTrue(FilterClause.negate(FieldFilter("source", FilterOp.EQ, "x")).matches(md))

    def test_match_all_is_identity(self):
        self.assertTrue(MATCH_ALL.matches({}))


# ---------------------------------------------------------------------------
# Tenant isolation + ACL
# ---------------------------------------------------------------------------
class TestSecurity(unittest.TestCase):
    def test_tenant_isolation_raises_on_leak(self):
        hits = [cand("a", tenant="t1"), cand("b", tenant="t2")]
        from backend.retrieval.retriever import _to_hit

        with self.assertRaises(TenantIsolationError):
            enforce_tenant_isolation([_to_hit(c) for c in hits], "t1")

    def test_acl_wildcard_visible(self):
        from backend.retrieval.retriever import _to_hit

        h = _to_hit(cand("a", acl={"allow": ["*"]}))
        self.assertEqual(len(apply_acl([h], frozenset())), 1)

    def test_acl_principal_match(self):
        from backend.retrieval.retriever import _to_hit

        h = _to_hit(cand("a", acl={"allow": ["group:finance"]}))
        self.assertEqual(len(apply_acl([h], frozenset({"group:finance"}))), 1)
        self.assertEqual(len(apply_acl([h], frozenset({"group:eng"}))), 0)

    def test_acl_explicit_deny_wins(self):
        from backend.retrieval.retriever import _to_hit

        h = _to_hit(cand("a", acl={"allow": ["*"], "deny": ["user:bob"]}))
        self.assertEqual(len(apply_acl([h], frozenset({"user:bob"}))), 0)

    def test_no_acl_defaults_private(self):
        from backend.retrieval.retriever import _to_hit

        h = _to_hit(cand("a"))  # no acl key
        self.assertEqual(len(apply_acl([h], frozenset({"user:any"}))), 0)


# ---------------------------------------------------------------------------
# Retriever end-to-end
# ---------------------------------------------------------------------------
class TestRetriever(unittest.TestCase):
    def _index(self):
        return FakeVectorIndex(
            [
                cand("a", score=0.9, text="alpha beta gamma", acl={"allow": ["*"]}, source="conf"),
                cand("b", score=0.8, text="beta delta", acl={"allow": ["*"]}, source="jira"),
                cand("c", score=0.7, text="epsilon", acl={"allow": ["user:x"]}, source="conf"),
                cand("d", score=0.6, text="gamma omega", acl={"allow": ["*"]}, source="conf"),
            ]
        )

    def test_basic_semantic_search(self):
        r = Retriever(index=self._index())
        q = RetrievalQuery(text="alpha", tenant_id="t1", acl_principals=frozenset())
        res = r.retrieve(q)
        # 'c' is dropped by ACL (user:x not in principals); 3 remain.
        self.assertEqual([h.chunk_id for h in res.hits], ["a", "b", "d"])
        self.assertEqual(res.page.total_candidates, 3)

    def test_metadata_filter(self):
        r = Retriever(index=self._index())
        q = RetrievalQuery(
            text="alpha",
            tenant_id="t1",
            acl_principals=frozenset({"*"}),
            filters=FilterClause.all_of(FieldFilter("source", FilterOp.EQ, "conf")),
        )
        res = r.retrieve(q)
        # 'c' is ACL-restricted to user:x and dropped; conf docs a,d remain.
        self.assertEqual(sorted(h.chunk_id for h in res.hits), ["a", "d"])

    def test_pagination(self):
        r = Retriever(index=self._index())
        q = RetrievalQuery(
            text="alpha",
            tenant_id="t1",
            acl_principals=frozenset({"*"}),
            pagination=Pagination(offset=1, limit=2),
        )
        res = r.retrieve(q)
        # Surviving order after ACL drop of 'c': [a, b, d]; page offset1 lim2.
        self.assertEqual([h.chunk_id for h in res.hits], ["b", "d"])
        self.assertFalse(res.page.has_more)

    def test_min_score_floor(self):
        r = Retriever(index=self._index())
        q = RetrievalQuery(
            text="alpha", tenant_id="t1", acl_principals=frozenset({"*"}), min_score=0.75
        )
        res = r.retrieve(q)
        self.assertEqual([h.chunk_id for h in res.hits], ["a", "b"])

    def test_tenant_isolation_defence(self):
        idx = LeakyVectorIndex([cand("a", tenant="t1"), cand("b", tenant="t2")])
        r = Retriever(index=idx)
        q = RetrievalQuery(text="x", tenant_id="t1")
        with self.assertRaises(TenantIsolationError):
            r.retrieve(q)

    def test_index_failure_wrapped(self):
        r = Retriever(index=BrokenVectorIndex())
        q = RetrievalQuery(text="x", tenant_id="t1")
        with self.assertRaises(IndexUnavailableError):
            r.retrieve(q)

    def test_empty_result(self):
        r = Retriever(index=FakeVectorIndex([]))
        q = RetrievalQuery(text="x", tenant_id="t1")
        res = r.retrieve(q)
        self.assertTrue(res.is_empty)
        self.assertFalse(res.page.has_more)


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------
class TestReranking(unittest.TestCase):
    def test_lexical_rerank_reorders(self):
        idx = FakeVectorIndex(
            [
                cand("a", score=0.91, text="totally unrelated", acl={"allow": ["*"]}),
                cand("b", score=0.90, text="machine learning models", acl={"allow": ["*"]}),
            ]
        )
        r = Retriever(index=idx, reranker=LexicalOverlapReranker(alpha=0.2))
        q = RetrievalQuery(
            text="machine learning", tenant_id="t1", acl_principals=frozenset({"*"})
        )
        res = r.retrieve(q)
        # 'b' has lexical overlap; with low alpha it should win despite lower sim.
        self.assertEqual(res.hits[0].chunk_id, "b")
        self.assertTrue(res.diagnostics.reranked)

    def test_safe_rerank_degrades_on_error(self):
        class Boom:
            def rerank(self, query, hits):
                raise RuntimeError("nope")

        from backend.retrieval.retriever import _to_hit

        hits = [_to_hit(cand("a"))]
        out, degraded = safe_rerank(Boom(), RetrievalQuery(text="x", tenant_id="t1"), hits)
        self.assertTrue(degraded)
        self.assertEqual(len(out), 1)

    def test_identity_reranker_preserves_order(self):
        from backend.retrieval.retriever import _to_hit

        hits = [_to_hit(cand("a", score=0.9)), _to_hit(cand("b", score=0.8))]
        out = IdentityReranker().rerank(RetrievalQuery(text="x", tenant_id="t1"), hits)
        self.assertEqual([h.chunk_id for h in out], ["a", "b"])


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------
class TestQueryExpansion(unittest.TestCase):
    def test_synonym_expander_adds_terms(self):
        exp = SynonymExpander(synonyms={"car": ["automobile", "vehicle"]})
        q = RetrievalQuery(text="fast car", tenant_id="t1")
        res = exp.expand(q)
        self.assertIn("automobile", res.query.text)
        self.assertEqual(set(res.expanded_terms), {"automobile", "vehicle"})

    def test_safe_expand_degrades(self):
        class Boom:
            def expand(self, query):
                raise RuntimeError("nope")

        q = RetrievalQuery(text="x", tenant_id="t1")
        res = safe_expand(Boom(), q)
        self.assertTrue(res.degraded)
        self.assertEqual(res.query.text, "x")

    def test_expansion_runs_in_retriever(self):
        idx = FakeVectorIndex([cand("a", text="automobile review", acl={"allow": ["*"]})])
        r = Retriever(index=idx, expander=SynonymExpander(synonyms={"car": ["automobile"]}))
        q = RetrievalQuery(
            text="car",
            tenant_id="t1",
            acl_principals=frozenset({"*"}),
            enable_query_expansion=True,
        )
        res = r.retrieve(q)
        self.assertIn("automobile", res.diagnostics.expanded_terms)

    def test_composite_expander(self):
        e = CompositeExpander(
            [
                SynonymExpander(synonyms={"car": ["automobile"]}),
                SynonymExpander(synonyms={"automobile": ["vehicle"]}),
            ]
        )
        q = RetrievalQuery(text="car", tenant_id="t1")
        res = e.expand(q)
        self.assertIn("automobile", res.expanded_terms)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
class TestMetrics(unittest.TestCase):
    def test_success_metrics_emitted(self):
        m = InMemoryMetrics()
        idx = FakeVectorIndex([cand("a", acl={"allow": ["*"]})])
        r = Retriever(index=idx, metrics=m)
        r.retrieve(RetrievalQuery(text="x", tenant_id="t1", acl_principals=frozenset({"*"})))
        self.assertEqual(m.counter(MetricNames.QUERIES), 1)
        self.assertEqual(m.counter(MetricNames.SUCCESS), 1)
        self.assertTrue(m.last_event().success)

    def test_failure_metrics_emitted(self):
        m = InMemoryMetrics()
        r = Retriever(index=BrokenVectorIndex(), metrics=m)
        with self.assertRaises(IndexUnavailableError):
            r.retrieve(RetrievalQuery(text="x", tenant_id="t1"))
        self.assertEqual(m.counter(MetricNames.FAILURE), 1)
        self.assertFalse(m.last_event().success)

    def test_acl_drop_counted(self):
        m = InMemoryMetrics()
        idx = FakeVectorIndex(
            [cand("a", acl={"allow": ["*"]}), cand("b", acl={"allow": ["user:x"]})]
        )
        r = Retriever(index=idx, metrics=m)
        r.retrieve(RetrievalQuery(text="x", tenant_id="t1", acl_principals=frozenset()))
        self.assertEqual(m.counter(MetricNames.ACL_DROPPED), 1)


# ---------------------------------------------------------------------------
# Hybrid retrieval
# ---------------------------------------------------------------------------
class TestHybridRetriever(unittest.TestCase):
    def test_fusion_promotes_keyword_hit(self):
        idx = FakeVectorIndex(
            [
                cand("a", score=0.9, acl={"allow": ["*"]}),
                cand("b", score=0.5, acl={"allow": ["*"]}),
            ]
        )
        kw = FakeKeywordIndex(["b", "a"])  # keyword strongly prefers b
        hr = HybridRetriever(index=idx, keyword_index=kw)
        res = hr.retrieve(
            RetrievalQuery(text="x", tenant_id="t1", acl_principals=frozenset({"*"}))
        )
        self.assertEqual({h.chunk_id for h in res.hits}, {"a", "b"})

    def test_overfetch_on_filters(self):
        idx = FakeVectorIndex(
            [cand(str(i), score=1.0 - i / 100, source="conf" if i % 2 else "jira",
                  acl={"allow": ["*"]}) for i in range(20)]
        )
        hr = HybridRetriever(index=idx, overfetch_factor=3)
        q = RetrievalQuery(
            text="x",
            tenant_id="t1",
            acl_principals=frozenset({"*"}),
            top_k=4,
            filters=FilterClause.all_of(FieldFilter("source", FilterOp.EQ, "jira")),
            pagination=Pagination(offset=0, limit=4),
        )
        res = hr.retrieve(q)
        # With overfetch the filter still yields a full page of jira docs.
        self.assertEqual(res.page.returned, 4)
        self.assertTrue(all(h.source == "jira" for h in res.hits))

    def test_pure_vector_when_no_keyword_index(self):
        idx = FakeVectorIndex([cand("a", acl={"allow": ["*"]})])
        hr = HybridRetriever(index=idx)
        res = hr.retrieve(
            RetrievalQuery(text="x", tenant_id="t1", acl_principals=frozenset({"*"}))
        )
        self.assertEqual(res.page.returned, 1)


# ---------------------------------------------------------------------------
# Downstream contract serialization
# ---------------------------------------------------------------------------
class TestContract(unittest.TestCase):
    def test_result_serialization(self):
        idx = FakeVectorIndex([cand("a", acl={"allow": ["*"]})])
        r = Retriever(index=idx)
        res = r.retrieve(
            RetrievalQuery(text="x", tenant_id="t1", acl_principals=frozenset({"*"}))
        )
        d = res.to_dict()
        self.assertIn("hits", d)
        self.assertIn("page", d)
        self.assertIn("diagnostics", d)
        self.assertEqual(d["hits"][0]["chunk_id"], "a")
        self.assertEqual(res.texts(), [h.text for h in res.hits])


if __name__ == "__main__":
    unittest.main(verbosity=2)
