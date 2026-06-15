"""Zendesk connector normalizer.

Maps a Zendesk ticket payload (as emitted by the Slice 1.1 Zendesk connector)
into a CanonicalDocument. The expected raw shape is the Zendesk Ticket API
object, optionally enriched with a list of comments.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import CanonicalDocument, Provenance, SourceMetadata
from .base import Normalizer, NormalizationError, clean_text


class ZendeskNormalizer(Normalizer):
    connector_type = "zendesk"

    def normalize(self, raw: Dict[str, Any], *, connector_id: str) -> CanonicalDocument:
        if not isinstance(raw, dict):
            raise NormalizationError("zendesk raw record must be a dict")

        ticket_id = raw.get("id")
        if ticket_id is None:
            raise NormalizationError("zendesk record missing required field 'id'")

        subject = clean_text(raw.get("subject") or "")
        description = clean_text(raw.get("description") or "")
        comments = self._collect_comments(raw)

        body_parts: List[str] = []
        if description:
            body_parts.append(description)
        body_parts.extend(comments)
        body = "\n\n".join(p for p in body_parts if p).strip()

        title = subject or f"Zendesk ticket #{ticket_id}"

        provenance = Provenance(
            connector_id=connector_id,
            connector_type=self.connector_type,
            source_id=str(ticket_id),
            source_type="ticket",
            source_url=raw.get("url"),
            source_created_at=raw.get("created_at"),
            source_updated_at=raw.get("updated_at"),
            sync_cursor=raw.get("_sync_cursor"),
        )

        source_metadata = SourceMetadata(
            fields={
                "status": raw.get("status"),
                "priority": raw.get("priority"),
                "type": raw.get("type"),
                "tags": raw.get("tags", []),
                "requester_id": raw.get("requester_id"),
                "assignee_id": raw.get("assignee_id"),
                "organization_id": raw.get("organization_id"),
                "group_id": raw.get("group_id"),
                "custom_fields": raw.get("custom_fields", []),
                "satisfaction_rating": raw.get("satisfaction_rating"),
            }
        )

        return CanonicalDocument(
            title=title,
            body=body,
            provenance=provenance,
            source_metadata=source_metadata,
            language=raw.get("locale"),
        )

    @staticmethod
    def _collect_comments(raw: Dict[str, Any]) -> List[str]:
        comments = raw.get("comments") or []
        out: List[str] = []
        for c in comments:
            if not isinstance(c, dict):
                continue
            text = c.get("body") or c.get("html_body") or c.get("plain_body") or ""
            cleaned = clean_text(text)
            if cleaned:
                author = c.get("author_id")
                prefix = f"[comment by {author}] " if author is not None else ""
                out.append(prefix + cleaned)
        return out
