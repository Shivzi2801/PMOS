"""
PMOS S1.5 — Index Fan-Out
chunker.py

The chunking pipeline.

Responsibilities:
  * Accept a canonical document view (the minimal fields S1.5 needs from S1.2/
    S1.4 — see `CanonicalDocumentView`).
  * Apply the sizing/overlap policy (chunk_strategy.py) to the normalized text.
  * Materialize DocumentChunk objects with stable identity and enriched
    metadata.

Identity:
  chunk_id is deterministic: a UUIDv5 over (tenant_id, document_id, ordinal).
  Determinism means re-chunking the same document produces the same chunk_ids,
  which the reconciler relies on to detect "missing" vs "new" chunks without a
  side table of random ids.

Metadata enrichment (additive, ACL-safe):
  * ordinal           — 0-based position within the document
  * span_start/end    — character offsets into the source text
  * char_len          — chunk length
  * overlap_prev      — overlap chars shared with previous chunk
  * source_type       — carried from the document view (e.g. "zendesk_ticket")
  * normalized_at     — timestamp from S1.2 normalization, if provided

Content safety: enrichment never copies raw entity values or ACL principals
into free-form metadata; entity_ids and source_acl live in their own typed
fields on the chunk.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import uuid
from typing import Any, FrozenSet, List, Mapping, Optional, Tuple

from .chunk_strategy import ChunkPlanner, default_planner
from .document_chunk import DocumentChunk, build_chunk
from .errors import EmptyDocumentError
from .hashing import content_hash

# Stable namespace for deterministic chunk ids within PMOS S1.5.
_CHUNK_NAMESPACE = uuid.UUID("6f2d1c0a-2b3e-5a7d-9c4f-1e0b8a6d3f51")


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


@dataclasses.dataclass(frozen=True)
class CanonicalDocumentView:
    """
    The slice-local projection of a canonical document. S1.5 deliberately does
    not import the full S1.2 document type; it depends only on these fields,
    which keeps the indexing subsystem decoupled.
    """

    tenant_id: str
    document_id: str
    content: str
    entity_ids: Tuple[str, ...] = ()
    source_acl: FrozenSet[str] = frozenset()
    source_type: Optional[str] = None
    normalized_at: Optional[_dt.datetime] = None
    extra_metadata: Mapping[str, Any] = dataclasses.field(default_factory=dict)


def _chunk_id(tenant_id: str, document_id: str, ordinal: int) -> str:
    name = f"{tenant_id}:{document_id}:{ordinal}"
    return str(uuid.uuid5(_CHUNK_NAMESPACE, name))


class Chunker:
    def __init__(self, planner: Optional[ChunkPlanner] = None) -> None:
        self.planner = planner or default_planner()

    def chunk(self, doc: CanonicalDocumentView) -> List[DocumentChunk]:
        text = doc.content or ""
        if text.strip() == "":
            raise EmptyDocumentError(
                "document has no indexable content",
                tenant_id=doc.tenant_id,
                document_id=doc.document_id,
            )

        spans = self.planner.plan(text)
        chunks: List[DocumentChunk] = []
        created = _utcnow()
        prev_end: Optional[int] = None

        for ordinal, (start, end) in enumerate(spans):
            piece = text[start:end]
            overlap_prev = 0
            if prev_end is not None and start < prev_end:
                overlap_prev = prev_end - start
            prev_end = end

            metadata = self._enrich(
                doc=doc,
                ordinal=ordinal,
                start=start,
                end=end,
                overlap_prev=overlap_prev,
            )

            chunk = build_chunk(
                chunk_id=_chunk_id(doc.tenant_id, doc.document_id, ordinal),
                tenant_id=doc.tenant_id,
                document_id=doc.document_id,
                content=piece,
                content_hash=content_hash(piece),
                entity_ids=doc.entity_ids,
                source_acl=doc.source_acl,
                metadata=metadata,
                created_at=created,
            )
            chunks.append(chunk)

        return chunks

    def _enrich(
        self,
        *,
        doc: CanonicalDocumentView,
        ordinal: int,
        start: int,
        end: int,
        overlap_prev: int,
    ) -> dict:
        metadata = dict(doc.extra_metadata)
        metadata.update(
            {
                "ordinal": ordinal,
                "span_start": start,
                "span_end": end,
                "char_len": end - start,
                "overlap_prev": overlap_prev,
            }
        )
        if doc.source_type is not None:
            metadata["source_type"] = doc.source_type
        if doc.normalized_at is not None:
            metadata["normalized_at"] = doc.normalized_at.isoformat()
        return metadata
