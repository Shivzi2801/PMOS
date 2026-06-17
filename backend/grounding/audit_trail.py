"""
audit_trail.py
==============

Persist verification metadata as immutable audit records.

WHY THIS FILE EXISTS
--------------------
Enterprise and compliance use-cases require that any answer can be *replayed*
and *justified* after the fact: which retrievals were used, which citations,
what the verdict was, and when. This module produces and stores those records.

It is intentionally decoupled from any specific database. The default
implementation writes append-only JSON lines to disk, which is auditable and
trivial to ship to a real store later. The storage backend is an injected
interface (`AuditSink`) so production can swap in a database / object store
without touching the pipeline.

RESPONSIBILITIES
----------------
* Build an AuditRecord from a GroundingResult and request context.
* Persist it via the configured sink.
* Degrade gracefully: an audit write failure must never destroy the user's
  answer. It raises AuditWriteError, which the pipeline catches and converts
  into a warning.

CONTRACT
--------
AuditRecord:
    request_id
    answer_id
    retrieval_ids
    citation_ids
    grounding_timestamp
    verification_summary

DESIGN DECISIONS
----------------
* Records are append-only and never mutated — that is what makes them an audit
  trail rather than mutable state.
* Timestamps are UTC ISO-8601 for unambiguous cross-region compliance.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .errors import AuditWriteError
from .grounding_result import GroundingResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuditRecord:
    """Immutable record of a single verification event."""

    request_id: str
    answer_id: str
    retrieval_ids: list[str]
    citation_ids: list[str]
    grounding_timestamp: str
    verification_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "answer_id": self.answer_id,
            "retrieval_ids": list(self.retrieval_ids),
            "citation_ids": list(self.citation_ids),
            "grounding_timestamp": self.grounding_timestamp,
            "verification_summary": dict(self.verification_summary),
        }


# --------------------------------------------------------------------------- #
# Storage abstraction
# --------------------------------------------------------------------------- #
class AuditSink(ABC):
    """Pluggable destination for audit records."""

    @abstractmethod
    def write(self, record: AuditRecord) -> None:  # pragma: no cover - interface
        ...


class InMemoryAuditSink(AuditSink):
    """Stores records in memory. Useful for tests and ephemeral environments."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = threading.Lock()

    def write(self, record: AuditRecord) -> None:
        with self._lock:
            self._records.append(record)

    @property
    def records(self) -> list[AuditRecord]:
        with self._lock:
            return list(self._records)


class JsonlFileAuditSink(AuditSink):
    """Append-only JSON-lines file sink.

    Each record is one line of JSON. Append-only semantics give us an immutable,
    greppable, replayable audit log with zero external dependencies.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# --------------------------------------------------------------------------- #
# Audit trail facade
# --------------------------------------------------------------------------- #
class AuditTrail:
    """Builds and persists audit records with graceful failure handling."""

    def __init__(self, sink: Optional[AuditSink] = None) -> None:
        self.sink = sink or InMemoryAuditSink()

    def build_record(
        self,
        *,
        request_id: str,
        result: GroundingResult,
        retrieval_ids: list[str],
        citation_ids: list[str],
    ) -> AuditRecord:
        summary = {
            "verification_status": result.verification_status.value,
            "confidence_score": result.confidence_score,
            "hallucination_risk": result.hallucination_risk.value,
            "citation_coverage": result.citation_coverage,
            "verified_claim_count": len(result.verified_claims),
            "unsupported_claim_count": len(result.unsupported_claims),
            "warnings": list(result.warnings),
        }
        return AuditRecord(
            request_id=request_id,
            answer_id=result.answer_id,
            retrieval_ids=list(retrieval_ids),
            citation_ids=list(citation_ids),
            grounding_timestamp=_utc_now_iso(),
            verification_summary=summary,
        )

    def persist(self, record: AuditRecord) -> None:
        """Persist a record, raising AuditWriteError on failure.

        The pipeline is responsible for catching this and degrading gracefully.
        """
        try:
            self.sink.write(record)
        except Exception as exc:
            raise AuditWriteError(
                "Failed to persist audit record.",
                details={
                    "request_id": record.request_id,
                    "answer_id": record.answer_id,
                    "cause": str(exc),
                },
            ) from exc
