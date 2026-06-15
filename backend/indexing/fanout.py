"""
PMOS S1.5 — Index Fan-Out
fanout.py

The index fan-out orchestrator.

Pipeline (slice requirement #4):

  canonical document
    -> chunk            (chunker.Chunker)
    -> deduplicate      (dedup.Deduplicator)
    -> embed            (caller-supplied Embedder; embeddings are out of scope)
    -> partition payload(qdrant_contract.build_payload)
    -> dispatch         (one or more VectorStore targets)

Design choices:

  * Multiple index targets. The orchestrator dispatches the SAME unique chunk
    set to every registered target (e.g. primary store + a shadow store during
    migration). "Fan-out" = one chunk set, many targets.

  * Per-target isolation. A failure writing to target B does not roll back a
    successful write to target A. Because upserts are idempotent (keyed by
    chunk_id), a later retry of B is safe and will not duplicate in A. When some
    targets fail, a PartialIndexError is raised carrying per-target outcomes so
    the caller (or a retry worker) can retry only the failed targets.

  * Retry. Transient VectorStore failures (retryable=True) are retried in-process
    with bounded exponential backoff via RetryPolicy. Non-retryable rejections
    short-circuit immediately.

  * Metrics. chunks_created / deduplicated / indexed counters and an
    indexing_latency_ms histogram are emitted, all labeled by tenant_id.

The embedder is injected as a callable so S1.5 carries no model dependency:
    Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]
"""

from __future__ import annotations

import dataclasses
import time
from typing import Callable, Dict, List, Mapping, Sequence, Tuple

from .chunker import CanonicalDocumentView, Chunker
from .dedup import Deduplicator
from .document_chunk import DocumentChunk
from .errors import (
    IndexingError,
    PartialIndexError,
    VectorStoreError,
)
from .metrics import (
    CHUNKS_CREATED,
    CHUNKS_DEDUPLICATED,
    CHUNKS_INDEXED,
    INDEXING_LATENCY_MS,
    LatencyTimer,
    MetricsSink,
)
from .qdrant_contract import build_payload
from .vector_store import IndexPoint, VectorStore

Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


@dataclasses.dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.05
    max_delay_s: float = 1.0

    def delay_for(self, attempt: int) -> float:
        # attempt is 1-based
        delay = self.base_delay_s * (2 ** (attempt - 1))
        return min(delay, self.max_delay_s)


@dataclasses.dataclass(frozen=True)
class FanOutResult:
    document_id: str
    tenant_id: str
    chunks_created: int
    chunks_deduplicated: int
    chunks_indexed: int
    targets_succeeded: Tuple[str, ...]
    targets_failed: Tuple[str, ...]

    @property
    def fully_indexed(self) -> bool:
        return len(self.targets_failed) == 0


class IndexFanOut:
    def __init__(
        self,
        *,
        chunker: Chunker,
        deduplicator: Deduplicator,
        embedder: Embedder,
        targets: Sequence[VectorStore],
        metrics: MetricsSink,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not targets:
            raise ValueError("at least one index target is required")
        self.chunker = chunker
        self.deduplicator = deduplicator
        self.embedder = embedder
        self.targets = list(targets)
        self.metrics = metrics
        self.retry = retry or RetryPolicy()
        self._sleep = sleep

    def index_document(self, doc: CanonicalDocumentView) -> FanOutResult:
        labels = {"tenant_id": doc.tenant_id}

        with LatencyTimer(self.metrics, INDEXING_LATENCY_MS, **labels):
            chunks = self.chunker.chunk(doc)
            self.metrics.incr(CHUNKS_CREATED, len(chunks), **labels)

            dedup = self.deduplicator.deduplicate(chunks)
            self.metrics.incr(
                CHUNKS_DEDUPLICATED, dedup.deduplicated_count, **labels
            )

            if not dedup.unique:
                # Everything was a duplicate; nothing to index. Still a success.
                return FanOutResult(
                    document_id=doc.document_id,
                    tenant_id=doc.tenant_id,
                    chunks_created=len(chunks),
                    chunks_deduplicated=dedup.deduplicated_count,
                    chunks_indexed=0,
                    targets_succeeded=tuple(t.name for t in self.targets),
                    targets_failed=(),
                )

            points = self._build_points(dedup.unique)
            succeeded, failures = self._dispatch(points, doc.tenant_id)

            if succeeded:
                # all succeeded targets indexed the same unique set
                self.metrics.incr(
                    CHUNKS_INDEXED, len(points) * len(succeeded), **labels
                )

            result = FanOutResult(
                document_id=doc.document_id,
                tenant_id=doc.tenant_id,
                chunks_created=len(chunks),
                chunks_deduplicated=dedup.deduplicated_count,
                chunks_indexed=len(points) if succeeded else 0,
                targets_succeeded=tuple(succeeded),
                targets_failed=tuple(failures.keys()),
            )

        if failures:
            raise PartialIndexError(
                "one or more index targets failed",
                failures=failures,
                tenant_id=doc.tenant_id,
                document_id=doc.document_id,
            )
        return result

    def reindex_chunks(
        self, tenant_id: str, chunks: Sequence[DocumentChunk]
    ) -> Tuple[List[str], Dict[str, IndexingError]]:
        """
        Repair path used by the reconciler. Writes an already-known,
        already-deduplicated chunk set directly to all targets, BYPASSING the
        dedup store. Dedup must be bypassed here because the chunks are known to
        be correct/expected — a missing or stale vector means the store lost the
        write, even though the dedup ledger still records the content as "seen".
        Idempotent upserts make redundant writes harmless. Returns
        (succeeded, failures) just like the normal dispatch path.
        """
        if not chunks:
            return ([t.name for t in self.targets], {})
        points = self._build_points(chunks)
        succeeded, failures = self._dispatch(points, tenant_id)
        if succeeded:
            self.metrics.incr(
                CHUNKS_INDEXED, len(points) * len(succeeded), tenant_id=tenant_id
            )
        return succeeded, failures

    # --- internals ---------------------------------------------------------

    def _build_points(
        self, chunks: Sequence[DocumentChunk]
    ) -> List[IndexPoint]:
        vectors = self.embedder([c.content for c in chunks])
        if len(vectors) != len(chunks):
            raise IndexingError("embedder returned wrong number of vectors")
        points: List[IndexPoint] = []
        for chunk, vec in zip(chunks, vectors):
            points.append(
                IndexPoint(
                    id=chunk.chunk_id,
                    tenant_id=chunk.tenant_id,
                    vector=tuple(float(x) for x in vec),
                    payload=build_payload(chunk),
                )
            )
        return points

    def _dispatch(
        self, points: Sequence[IndexPoint], tenant_id: str
    ) -> Tuple[List[str], Dict[str, IndexingError]]:
        succeeded: List[str] = []
        failures: Dict[str, IndexingError] = {}

        for target in self.targets:
            try:
                self._upsert_with_retry(target, points, tenant_id)
                succeeded.append(target.name)
            except IndexingError as exc:
                # isolate: record and continue to next target
                failures[target.name] = exc

        return succeeded, failures

    def _upsert_with_retry(
        self,
        target: VectorStore,
        points: Sequence[IndexPoint],
        tenant_id: str,
    ) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                target.upsert(points)
                return
            except VectorStoreError as exc:
                exc.tenant_id = exc.tenant_id or tenant_id
                if not exc.retryable or attempt >= self.retry.max_attempts:
                    raise
                self._sleep(self.retry.delay_for(attempt))
