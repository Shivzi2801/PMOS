"""Context container types for the assembly layer (S1.7).

This module defines:

* The minimal Retrieval contract consumed from S1.6 (:class:`RetrievedChunk`,
  :class:`RetrievalResult`). These mirror the upstream Retrieval layer's
  output. If the real S1.6 module is importable, prefer importing those types;
  the definitions here are structural and field-compatible so assembly code
  depends only on attribute names, not the concrete class.
* :class:`ContextChunk` — a chunk after filtering/ranking, annotated with its
  assigned citation marker and token cost.
* :class:`ContextPackage` — the fully assembled, ordered context ready to be
  rendered into a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .citation_record import CitationRecord


# ---------------------------------------------------------------------------
# S1.6 Retrieval contract (consumed, not owned).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RetrievedChunk:
    """A single chunk produced by the Retrieval layer (S1.6).

    Attributes:
        chunk_id: Unique identifier of the chunk.
        document_id: Identifier of the parent document.
        content: The chunk text.
        score: Relevance score (higher is more relevant).
        acl_tags: Access-control tags governing visibility.
        source_uri: Optional source locator.
        title: Optional source title.
        metadata: Arbitrary passthrough metadata.
    """

    chunk_id: str
    document_id: str
    content: str
    score: float
    acl_tags: Tuple[str, ...] = ()
    source_uri: Optional[str] = None
    title: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    """The result envelope emitted by the Retrieval layer (S1.6).

    Attributes:
        query: The originating user query text.
        chunks: Ordered (by retrieval) sequence of retrieved chunks.
        metadata: Arbitrary passthrough metadata from retrieval.
    """

    query: str
    chunks: Sequence[RetrievedChunk]
    metadata: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context assembly types (owned by S1.7).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ContextChunk:
    """A retrieved chunk that survived filtering and was selected for context.

    Attributes:
        source: The originating retrieved chunk.
        marker: Citation marker assigned during assembly, e.g. ``"[1]"``.
        token_cost: Tokens this chunk contributes to the context window.
    """

    source: RetrievedChunk
    marker: str
    token_cost: int

    @property
    def chunk_id(self) -> str:
        return self.source.chunk_id

    @property
    def content(self) -> str:
        return self.source.content

    @property
    def score(self) -> float:
        return self.source.score


@dataclass(frozen=True)
class ContextPackage:
    """Fully assembled context ready for prompt construction.

    Attributes:
        query: The originating user query.
        chunks: Ordered chunks selected for the context window.
        citations: Citation records, one per selected chunk.
        used_context_tokens: Total tokens consumed by selected chunks.
        dropped_chunk_ids: Chunk ids removed by dedup/ACL/budget, for audit.
        metadata: Passthrough metadata accumulated during assembly.
    """

    query: str
    chunks: Sequence[ContextChunk]
    citations: Sequence[CitationRecord]
    used_context_tokens: int
    dropped_chunk_ids: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return len(self.chunks) == 0

    def render_context_block(self) -> str:
        """Render the ordered chunks into a single context string."""
        parts: List[str] = []
        for chunk in self.chunks:
            parts.append(f"{chunk.marker} {chunk.content}")
        return "\n\n".join(parts)

    def render_citations_block(self) -> str:
        """Render the citation list into a single string."""
        return "\n".join(c.render() for c in self.citations)
