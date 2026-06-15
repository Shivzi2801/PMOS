"""
PMOS S1.5 — Index Fan-Out
test_indexing.py

Pure-stdlib tests (unittest). Run: python -m unittest test_indexing -v
Covers: contract validation, hashing stability, chunking sizing/overlap,
dedup + collision guard, fan-out happy path + partial failure + retry,
vector store ACL/tenant isolation, payload safety, and reconciliation
(missing / stale / orphan / repair).
"""

from __future__ import annotations

import datetime as _dt
import unittest
from typing import List, Sequence

from indexing.chunk_strategy import (
    ChunkOverlap,
    ChunkPlanner,
    ChunkSizing,
)
from indexing.chunker import CanonicalDocumentView, Chunker
from indexing.dedup import Deduplicator, InMemoryDedupStore
from indexing.document_chunk import build_chunk
from indexing.errors import (
    ContractViolation,
    EmptyDocumentError,
    HashCollisionError,
    PartialIndexError,
    VectorStoreRejected,
    VectorStoreUnavailable,
)
from indexing.fanout import IndexFanOut, RetryPolicy
from indexing.hashing import content_hash, is_valid_hash, verify
from indexing.metrics import (
    CHUNKS_CREATED,
    CHUNKS_DEDUPLICATED,
    CHUNKS_INDEXED,
    RECONCILIATION_FAILURES,
    InMemoryMetricsSink,
)
from indexing.qdrant_contract import PAYLOAD_ACL, build_payload
from indexing.reconciler import ExpectedChunkSource, Reconciler
from indexing.vector_store import InMemoryVectorStore, IndexPoint, VectorStore


def _doc(text: str, *, tenant="t1", doc_id="d1", acl=("alice",), entities=()):
    return CanonicalDocumentView(
        tenant_id=tenant,
        document_id=doc_id,
        content=text,
        entity_ids=tuple(entities),
        source_acl=frozenset(acl),
        source_type="zendesk_ticket",
    )


def _fake_embedder(texts: Sequence[str]) -> List[List[float]]:
    # deterministic 3-dim "embedding": length-based, stable per text
    out = []
    for t in texts:
        out.append([float(len(t) % 7 + 1), 1.0, 0.5])
    return out


def _build_fanout(targets, *, retry=None, metrics=None):
    return IndexFanOut(
        chunker=Chunker(),
        deduplicator=Deduplicator(InMemoryDedupStore()),
        embedder=_fake_embedder,
        targets=targets,
        metrics=metrics or InMemoryMetricsSink(),
        retry=retry or RetryPolicy(max_attempts=3, base_delay_s=0.0),
        sleep=lambda s: None,
    )


# --- contract --------------------------------------------------------------

class ContractTests(unittest.TestCase):
    def test_valid_chunk(self):
        c = build_chunk(
            chunk_id="c1",
            tenant_id="t1",
            document_id="d1",
            content="hello",
            content_hash=content_hash("hello"),
            source_acl=frozenset({"alice"}),
        )
        self.assertTrue(c.is_retrievable)

    def test_empty_acl_not_retrievable(self):
        c = build_chunk(
            chunk_id="c1",
            tenant_id="t1",
            document_id="d1",
            content="hello",
            content_hash=content_hash("hello"),
            source_acl=frozenset(),
        )
        self.assertFalse(c.is_retrievable)

    def test_bad_hash_rejected(self):
        with self.assertRaises(ContractViolation):
            build_chunk(
                chunk_id="c1",
                tenant_id="t1",
                document_id="d1",
                content="hello",
                content_hash="not-a-hash",
            )

    def test_missing_tenant_rejected(self):
        with self.assertRaises(ContractViolation):
            build_chunk(
                chunk_id="c1",
                tenant_id="",
                document_id="d1",
                content="x",
                content_hash=content_hash("x"),
            )

    def test_safe_descriptor_excludes_content(self):
        c = build_chunk(
            chunk_id="c1",
            tenant_id="t1",
            document_id="d1",
            content="secret text",
            content_hash=content_hash("secret text"),
            source_acl=frozenset({"alice"}),
        )
        desc = c.safe_descriptor()
        self.assertNotIn("content", desc)
        self.assertNotIn("entity_ids", desc)
        self.assertEqual(desc["acl_size"], 1)


# --- hashing ---------------------------------------------------------------

class HashingTests(unittest.TestCase):
    def test_stable(self):
        self.assertEqual(content_hash("hello"), content_hash("hello"))

    def test_normalization_equivalence(self):
        self.assertEqual(content_hash("a \n b"), content_hash("a\n b"))

    def test_valid_hash_shape(self):
        self.assertTrue(is_valid_hash(content_hash("x")))
        self.assertFalse(is_valid_hash("ABC"))

    def test_verify(self):
        self.assertTrue(verify("data", content_hash("data")))
        self.assertFalse(verify("data", content_hash("other")))


# --- chunking --------------------------------------------------------------

class ChunkingTests(unittest.TestCase):
    def test_progress_and_coverage(self):
        planner = ChunkPlanner(
            ChunkSizing(target_size=50, max_size=60, min_size=10),
            ChunkOverlap(overlap=10),
        )
        text = "word " * 100
        spans = planner.plan(text)
        self.assertTrue(spans)
        # spans cover start..end with forward progress
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], len(text))
        for (s, e) in spans:
            self.assertLess(s, e)
            self.assertLessEqual(e - s, 60)

    def test_overlap_present(self):
        planner = ChunkPlanner(
            ChunkSizing(target_size=40, max_size=50, min_size=5),
            ChunkOverlap(overlap=10),
        )
        text = "x" * 200
        spans = planner.plan(text)
        # consecutive spans should overlap by ~10 where possible
        overlaps = [spans[i][1] - spans[i + 1][0] for i in range(len(spans) - 1)]
        self.assertTrue(any(o > 0 for o in overlaps))

    def test_empty_document_raises(self):
        with self.assertRaises(EmptyDocumentError):
            Chunker().chunk(_doc("   "))

    def test_deterministic_chunk_ids(self):
        a = Chunker().chunk(_doc("hello world " * 30))
        b = Chunker().chunk(_doc("hello world " * 30))
        self.assertEqual([c.chunk_id for c in a], [c.chunk_id for c in b])

    def test_metadata_enrichment(self):
        chunks = Chunker().chunk(_doc("hello world " * 50))
        md = chunks[0].metadata
        self.assertEqual(md["ordinal"], 0)
        self.assertIn("span_start", md)
        self.assertIn("char_len", md)
        self.assertEqual(md["source_type"], "zendesk_ticket")


# --- dedup -----------------------------------------------------------------

class DedupTests(unittest.TestCase):
    def test_within_batch_dedup(self):
        d = Deduplicator(InMemoryDedupStore())
        c1 = build_chunk(chunk_id="c1", tenant_id="t1", document_id="d1",
                         content="same", content_hash=content_hash("same"))
        c2 = build_chunk(chunk_id="c2", tenant_id="t1", document_id="d1",
                         content="same", content_hash=content_hash("same"))
        res = d.deduplicate([c1, c2])
        self.assertEqual(len(res.unique), 1)
        self.assertEqual(res.deduplicated_count, 1)

    def test_cross_run_dedup(self):
        store = InMemoryDedupStore()
        d = Deduplicator(store)
        c = build_chunk(chunk_id="c1", tenant_id="t1", document_id="d1",
                        content="x", content_hash=content_hash("x"))
        self.assertEqual(len(d.deduplicate([c]).unique), 1)
        c2 = build_chunk(chunk_id="c2", tenant_id="t1", document_id="d2",
                         content="x", content_hash=content_hash("x"))
        self.assertEqual(len(d.deduplicate([c2]).unique), 0)

    def test_tenant_isolation(self):
        store = InMemoryDedupStore()
        d = Deduplicator(store)
        a = build_chunk(chunk_id="c1", tenant_id="t1", document_id="d1",
                        content="x", content_hash=content_hash("x"))
        b = build_chunk(chunk_id="c2", tenant_id="t2", document_id="d1",
                        content="x", content_hash=content_hash("x"))
        self.assertEqual(len(d.deduplicate([a]).unique), 1)
        # different tenant, same content => NOT a duplicate
        self.assertEqual(len(d.deduplicate([b]).unique), 1)

    def test_collision_guard(self):
        store = InMemoryDedupStore()
        d = Deduplicator(store)
        good = build_chunk(chunk_id="c1", tenant_id="t1", document_id="d1",
                           content="abc", content_hash=content_hash("abc"))
        d.deduplicate([good])
        # forge a chunk that reuses the hash but has different content length
        forged = build_chunk(chunk_id="c2", tenant_id="t1", document_id="d1",
                             content="abcdef", content_hash=content_hash("abc"))
        with self.assertRaises(HashCollisionError):
            d.deduplicate([forged])


# --- payload ---------------------------------------------------------------

class PayloadTests(unittest.TestCase):
    def test_payload_excludes_content(self):
        c = build_chunk(chunk_id="c1", tenant_id="t1", document_id="d1",
                        content="secret", content_hash=content_hash("secret"),
                        source_acl=frozenset({"alice", "bob"}),
                        metadata={"ordinal": 0, "source_type": "zendesk_ticket"})
        payload = build_payload(c)
        self.assertNotIn("content", payload)
        self.assertEqual(payload[PAYLOAD_ACL], ["alice", "bob"])
        self.assertEqual(payload["tenant_id"], "t1")


# --- vector store ----------------------------------------------------------

class VectorStoreTests(unittest.TestCase):
    def test_tenant_isolation_on_fetch(self):
        s = InMemoryVectorStore()
        s.upsert([IndexPoint("c1", "t1", (1.0, 0.0), {"document_id": "d1", "acl": ["alice"]})])
        s.upsert([IndexPoint("c2", "t2", (1.0, 0.0), {"document_id": "d1", "acl": ["alice"]})])
        self.assertEqual(s.fetch_ids("t1", "d1"), {"c1"})
        self.assertEqual(s.fetch_ids("t2", "d1"), {"c2"})

    def test_acl_filter_on_search(self):
        s = InMemoryVectorStore()
        s.upsert([IndexPoint("c1", "t1", (1.0, 0.0, 0.0),
                             {"document_id": "d1", "acl": ["alice"]})])
        # bob cannot see alice-only chunk
        hits = s.search("t1", (1.0, 0.0, 0.0), frozenset({"bob"}))
        self.assertEqual(hits, [])
        hits = s.search("t1", (1.0, 0.0, 0.0), frozenset({"alice"}))
        self.assertEqual(len(hits), 1)

    def test_idempotent_upsert(self):
        s = InMemoryVectorStore()
        p = IndexPoint("c1", "t1", (1.0,), {"document_id": "d1", "acl": ["a"]})
        s.upsert([p])
        s.upsert([p])
        self.assertEqual(s.all_ids("t1"), {"c1"})

    def test_bad_point_rejected(self):
        with self.assertRaises(VectorStoreRejected):
            IndexPoint("", "t1", (1.0,), {})


# --- fan-out ---------------------------------------------------------------

class _FlakyStore(VectorStore):
    """Fails the first `fail_n` upserts with a retryable error, then succeeds."""

    name = "flaky"

    def __init__(self, fail_n: int):
        self.fail_n = fail_n
        self.calls = 0
        self.inner = InMemoryVectorStore()

    def upsert(self, points):
        self.calls += 1
        if self.calls <= self.fail_n:
            raise VectorStoreUnavailable("transient")
        self.inner.upsert(points)

    def delete(self, tenant_id, ids):
        self.inner.delete(tenant_id, ids)

    def fetch_ids(self, tenant_id, document_id):
        return self.inner.fetch_ids(tenant_id, document_id)

    def search(self, tenant_id, qv, principals, limit=10):
        return self.inner.search(tenant_id, qv, principals, limit)

    def health(self):
        return True


class _DeadStore(VectorStore):
    name = "dead"

    def upsert(self, points):
        raise VectorStoreRejected("permanent schema mismatch")

    def delete(self, tenant_id, ids):  # pragma: no cover
        pass

    def fetch_ids(self, tenant_id, document_id):  # pragma: no cover
        return set()

    def search(self, tenant_id, qv, principals, limit=10):  # pragma: no cover
        return []

    def health(self):
        return False


class FanOutTests(unittest.TestCase):
    def test_happy_path(self):
        store = InMemoryVectorStore()
        metrics = InMemoryMetricsSink()
        fo = _build_fanout([store], metrics=metrics)
        result = fo.index_document(_doc("hello world " * 400))
        self.assertTrue(result.fully_indexed)
        self.assertGreater(result.chunks_created, 0)
        self.assertEqual(result.chunks_indexed, len(store.all_ids("t1")))
        self.assertEqual(
            metrics.counter_value(CHUNKS_CREATED, tenant_id="t1"),
            result.chunks_created,
        )
        self.assertGreater(
            metrics.counter_value(CHUNKS_INDEXED, tenant_id="t1"), 0
        )

    def test_retry_then_succeed(self):
        flaky = _FlakyStore(fail_n=2)
        fo = _build_fanout([flaky])
        result = fo.index_document(_doc("hello world " * 400))
        self.assertTrue(result.fully_indexed)
        self.assertEqual(flaky.calls, 3)  # 2 failures + 1 success

    def test_partial_failure_isolated(self):
        good = InMemoryVectorStore()
        bad = _DeadStore()
        fo = _build_fanout([good, bad])
        with self.assertRaises(PartialIndexError) as ctx:
            fo.index_document(_doc("hello world " * 400))
        # good target still got the writes; bad target is reported failed
        self.assertGreater(len(good.all_ids("t1")), 0)
        self.assertIn("dead", ctx.exception.failures)

    def test_full_dedup_indexes_nothing(self):
        store = InMemoryVectorStore()
        dedup = Deduplicator(InMemoryDedupStore())
        metrics = InMemoryMetricsSink()
        fo = IndexFanOut(
            chunker=Chunker(),
            deduplicator=dedup,
            embedder=_fake_embedder,
            targets=[store],
            metrics=metrics,
            retry=RetryPolicy(base_delay_s=0.0),
            sleep=lambda s: None,
        )
        doc = _doc("hello world " * 400)
        fo.index_document(doc)
        # second run: everything already seen => 0 indexed
        result = fo.index_document(doc)
        self.assertEqual(result.chunks_indexed, 0)
        self.assertGreater(result.chunks_deduplicated, 0)


# --- reconciler ------------------------------------------------------------

class _Source(ExpectedChunkSource):
    def __init__(self, docs, hashes):
        self._docs = docs
        self._hashes = hashes  # point_id -> expected hash (or absent)

    def documents(self):
        return list(self._docs)

    def expected_hash(self, point_id):
        return self._hashes.get(point_id)


class ReconcilerTests(unittest.TestCase):
    def _setup(self, doc):
        store = InMemoryVectorStore()
        fo = _build_fanout([store])
        chunker = Chunker()
        chunks = chunker.chunk(doc)
        hashes = {c.chunk_id: c.content_hash for c in chunks}
        return store, fo, chunker, chunks, hashes

    def test_detect_and_repair_missing(self):
        doc = _doc("hello world " * 400)
        store, fo, chunker, chunks, hashes = self._setup(doc)
        fo.index_document(doc)
        # simulate a dropped write: delete one point
        victim = chunks[0].chunk_id
        store.delete("t1", [victim])
        self.assertNotIn(victim, store.all_ids("t1"))

        metrics = InMemoryMetricsSink()
        rec = Reconciler(
            chunker=chunker,
            source=_Source([doc], hashes),
            targets=[store],
            fanout=fo,
            metrics=metrics,
        )
        report = rec.run(repair=True)
        self.assertEqual(report.drifted_documents, 1)
        self.assertIn(victim, store.all_ids("t1"))  # repaired
        self.assertGreater(report.chunks_reindexed, 0)

    def test_detect_orphan_and_delete(self):
        doc = _doc("hello world " * 400)
        store, fo, chunker, chunks, hashes = self._setup(doc)
        fo.index_document(doc)
        # inject an orphan point not in expected set
        store.upsert([IndexPoint("orphan-1", "t1", (1.0,),
                                 {"document_id": "d1", "acl": ["alice"]})])
        rec = Reconciler(
            chunker=chunker,
            source=_Source([doc], hashes),
            targets=[store],
            fanout=fo,
            metrics=InMemoryMetricsSink(),
        )
        report = rec.run(repair=True)
        self.assertEqual(report.orphans_deleted, 1)
        self.assertNotIn("orphan-1", store.all_ids("t1"))

    def test_detect_stale(self):
        doc = _doc("hello world " * 400)
        store, fo, chunker, chunks, hashes = self._setup(doc)
        fo.index_document(doc)
        # authority reports a different (old) hash for one present id => stale
        stale_id = chunks[1].chunk_id
        hashes[stale_id] = content_hash("OLD DIFFERENT CONTENT")
        rec = Reconciler(
            chunker=chunker,
            source=_Source([doc], hashes),
            targets=[store],
            fanout=fo,
            metrics=InMemoryMetricsSink(),
        )
        report = rec.run(repair=True)
        stale_found = any(stale_id in f.stale for f in report.findings)
        self.assertTrue(stale_found)

    def test_reconciliation_failure_counted(self):
        doc = _doc("hello world " * 400)
        store, fo, chunker, chunks, hashes = self._setup(doc)
        fo.index_document(doc)

        class _BrokenFetch(InMemoryVectorStore):
            name = "broken"

            def fetch_ids(self, tenant_id, document_id):
                from indexing.errors import ReconciliationError
                raise ReconciliationError("fetch failed", tenant_id=tenant_id)

        broken = _BrokenFetch()
        metrics = InMemoryMetricsSink()
        rec = Reconciler(
            chunker=chunker,
            source=_Source([doc], hashes),
            targets=[broken],
            fanout=fo,
            metrics=metrics,
        )
        report = rec.run(repair=True)
        self.assertGreaterEqual(report.failures, 1)
        self.assertGreaterEqual(
            metrics.counter_value(RECONCILIATION_FAILURES, tenant_id="t1"), 1
        )


if __name__ == "__main__":
    unittest.main()
