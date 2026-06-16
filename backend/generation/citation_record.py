"""Citation records for context assembly (S1.7).

A :class:`CitationRecord` ties a position in the assembled context back to the
originating source document, enabling the downstream LLM (and the end user) to
attribute claims to specific sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class CitationRecord:
    """An attributable reference to a source chunk used in the context.

    Attributes:
        marker: The in-text marker, e.g. ``"[1]"``, used inside the context.
        chunk_id: Identifier of the retrieved chunk.
        document_id: Identifier of the source document.
        source_uri: Human/machine reference to the source (path, URL, etc.).
        title: Optional human-readable title for the source.
        score: Relevance score carried from retrieval, for transparency.
        metadata: Arbitrary passthrough metadata from the source chunk.
    """

    marker: str
    chunk_id: str
    document_id: str
    source_uri: Optional[str] = None
    title: Optional[str] = None
    score: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        """Render a single human-readable citation line."""
        label = self.title or self.source_uri or self.document_id
        if self.source_uri and self.title:
            return f"{self.marker} {self.title} ({self.source_uri})"
        return f"{self.marker} {label}"
