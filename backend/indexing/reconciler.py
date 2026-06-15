"""
PMOS S1.5 — Index Fan-Out
reconciler.py

Nightly reconciler.

Purpose: the fan-out path can drop writes (partial failures, crashes, store
outages). The reconciler is the safety net that periodically compares the
*expected* chunk set for each document against what each index target actually
holds, and repairs drift.

Inputs:
  * An `ExpectedChunkSource` — the authority on what chunks SHOULD exist for a
    document. In PMOS this is derived by re-chunking the canonical document
    (deterministic chunk_ids from chunker.py make this a pure function of the
    document). S1.5 abstracts it behind an interface so the reconciler does not
    couple to the canonical store.
  * The same `VectorStore` targets the orchestrator writes to.

Detections (slice requirement #7):
  * MISSING chunks  — expected id absent from a target.
  * STALE vectors   — id present in a target but content_hash differs from the
                      expected chunk's hash (content changed; vector is stale),
                      OR id present in target but no longer expected (orphan).
  * Reindex         — repair by upserting missing/stale chunks and deleting
                      orphans. Uses the injected IndexFanOut for writes so the
                      same idempotent, ACL-safe path is reused.

Output:
  * A `ReconciliationReport` enumerating per-document, per-target findings and
    repair outcomes, plus aggregate counters. Reconciliation failures are
    counted into metrics (RECONCILIATION_FAILURES) and surfaced in the report
    rather than thrown, so one bad document does not abort the whole run.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from .chunker import CanonicalDocumentView, Chunker
from .document_chunk import DocumentChunk
from .errors import IndexingError, ReconciliationError
from .fanout import IndexFanOut
from .metrics import RECONCILIATION_FAILURES, MetricsSink
from .qdrant_contract import PAYLOAD_CONTENT_HASH
from .vector_store import VectorStore


class ExpectedChunkSource(abc.ABC):
    """Authority on the chunk set a document should currently have."""

    @abc.abstractmethod
    def documents(self) -> Iterable[CanonicalDocumentView]:
        """Iterate the documents in scope for this reconciliation run."""

    @abc.abstractmethod
    def expected_hash(self, point_id: str) -> str | None:
        """
        Return the expected content_hash for an indexed point id, or None if
        the id is no longer expected (orphan). Used for stale detection.
        """


@dataclasses.dataclass(frozen=True)
class DocumentFinding:
    tenant_id: str
    document_id: str
    target: str
    missing: Tuple[str, ...]
    stale: Tuple[str, ...]
    orphan: Tuple[str, ...]
    repaired: bool
    error: str | None = None

    @property
    def has_drift(self) -> bool:
        return bool(self.missing or self.stale or self.orphan)


@dataclasses.dataclass
class ReconciliationReport:
    findings: List[DocumentFinding] = dataclasses.field(default_factory=list)
    documents_scanned: int = 0
    drifted_documents: int = 0
    chunks_reindexed: int = 0
    orphans_deleted: int = 0
    failures: int = 0

    def add(self, finding: DocumentFinding) -> None:
        self.findings.append(finding)

    def summary(self) -> Mapping[str, int]:
        return {
            "documents_scanned": self.documents_scanned,
            "drifted_documents": self.drifted_documents,
            "chunks_reindexed": self.chunks_reindexed,
            "orphans_deleted": self.orphans_deleted,
            "failures": self.failures,
        }


class Reconciler:
    def __init__(
        self,
        *,
        chunker: Chunker,
        source: ExpectedChunkSource,
        targets: Sequence[VectorStore],
        fanout: IndexFanOut,
        metrics: MetricsSink,
    ) -> None:
        self.chunker = chunker
        self.source = source
        self.targets = list(targets)
        self.fanout = fanout
        self.metrics = metrics

    def run(self, *, repair: bool = True) -> ReconciliationReport:
        report = ReconciliationReport()

        for doc in self.source.documents():
            report.documents_scanned += 1
            try:
                self._reconcile_document(doc, report, repair=repair)
            except IndexingError as exc:
                report.failures += 1
                self.metrics.incr(
                    RECONCILIATION_FAILURES, 1, tenant_id=doc.tenant_id
                )
                report.add(
                    DocumentFinding(
                        tenant_id=doc.tenant_id,
                        document_id=doc.document_id,
                        target="*",
                        missing=(),
                        stale=(),
                        orphan=(),
                        repaired=False,
                        error=str(exc),
                    )
                )

        return report

    # --- internals ---------------------------------------------------------

    def _reconcile_document(
        self,
        doc: CanonicalDocumentView,
        report: ReconciliationReport,
        *,
        repair: bool,
    ) -> None:
        expected_chunks = self.chunker.chunk(doc)
        expected_by_id: Dict[str, DocumentChunk] = {
            c.chunk_id: c for c in expected_chunks
        }
        expected_ids: Set[str] = set(expected_by_id)

        document_drifted = False
        needs_repair_write = False

        for target in self.targets:
            try:
                actual_ids = target.fetch_ids(doc.tenant_id, doc.document_id)
            except IndexingError as exc:
                # treat as a per-target reconciliation failure (do not abort run)
                report.failures += 1
                self.metrics.incr(
                    RECONCILIATION_FAILURES, 1, tenant_id=doc.tenant_id
                )
                report.add(
                    DocumentFinding(
                        tenant_id=doc.tenant_id,
                        document_id=doc.document_id,
                        target=target.name,
                        missing=(),
                        stale=(),
                        orphan=(),
                        repaired=False,
                        error=str(exc),
                    )
                )
                continue

            missing = expected_ids - actual_ids
            orphan = actual_ids - expected_ids
            stale = self._detect_stale(actual_ids & expected_ids, expected_by_id)

            finding_drift = bool(missing or stale or orphan)
            document_drifted = document_drifted or finding_drift
            if missing or stale:
                needs_repair_write = True

            repaired = False
            if repair and orphan:
                try:
                    target.delete(doc.tenant_id, sorted(orphan))
                    report.orphans_deleted += len(orphan)
                    repaired = True
                except IndexingError as exc:
                    report.failures += 1
                    self.metrics.incr(
                        RECONCILIATION_FAILURES, 1, tenant_id=doc.tenant_id
                    )
                    report.add(
                        DocumentFinding(
                            tenant_id=doc.tenant_id,
                            document_id=doc.document_id,
                            target=target.name,
                            missing=tuple(sorted(missing)),
                            stale=tuple(sorted(stale)),
                            orphan=tuple(sorted(orphan)),
                            repaired=False,
                            error=str(exc),
                        )
                    )
                    continue

            report.add(
                DocumentFinding(
                    tenant_id=doc.tenant_id,
                    document_id=doc.document_id,
                    target=target.name,
                    missing=tuple(sorted(missing)),
                    stale=tuple(sorted(stale)),
                    orphan=tuple(sorted(orphan)),
                    repaired=repaired,
                )
            )

        if document_drifted:
            report.drifted_documents += 1

        # Repair missing/stale by writing the authoritative expected chunk set
        # directly via the idempotent reindex path. This BYPASSES dedup on
        # purpose: the dedup ledger still lists this content as "seen", so the
        # normal index path would write nothing — but the store actually lost
        # the vector. Idempotent upserts make redundant writes harmless.
        if repair and needs_repair_write:
            succeeded, failures = self.fanout.reindex_chunks(
                doc.tenant_id, expected_chunks
            )
            if failures:
                for tname, exc in failures.items():
                    report.failures += 1
                    self.metrics.incr(
                        RECONCILIATION_FAILURES, 1, tenant_id=doc.tenant_id
                    )
                    report.add(
                        DocumentFinding(
                            tenant_id=doc.tenant_id,
                            document_id=doc.document_id,
                            target=tname,
                            missing=(),
                            stale=(),
                            orphan=(),
                            repaired=False,
                            error=str(exc),
                        )
                    )
            if succeeded:
                report.chunks_reindexed += len(expected_chunks)

    def _detect_stale(
        self,
        present_ids: Set[str],
        expected_by_id: Mapping[str, DocumentChunk],
    ) -> Set[str]:
        """
        A present point is stale if the authority's expected content_hash for
        that id differs from the expected chunk's current hash. Because chunk
        ids are deterministic by ordinal, a content change at the same ordinal
        yields the same id with a new hash — exactly the stale case.
        """
        stale: Set[str] = set()
        for pid in present_ids:
            authoritative = self.source.expected_hash(pid)
            expected_now = expected_by_id[pid].content_hash
            if authoritative is None:
                # source no longer recognizes this id => treat as stale
                stale.add(pid)
            elif authoritative != expected_now:
                stale.add(pid)
        return stale
