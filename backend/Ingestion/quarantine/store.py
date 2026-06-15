"""Storage backends for quarantined records.

Two implementations are provided:
  * InMemoryQuarantineStore - default, used by tests and ephemeral runs.
  * FileQuarantineStore      - append-only JSONL, durable across restarts.

Both implement the QuarantineStore protocol so the service is storage-agnostic.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ..contracts import QuarantineRecord


class QuarantineStore(ABC):
    @abstractmethod
    def save(self, record: QuarantineRecord) -> None: ...

    @abstractmethod
    def get(self, quarantine_id: str) -> Optional[QuarantineRecord]: ...

    @abstractmethod
    def list(self) -> List[QuarantineRecord]: ...

    @abstractmethod
    def count(self) -> int: ...


class InMemoryQuarantineStore(QuarantineStore):
    def __init__(self) -> None:
        self._records: Dict[str, QuarantineRecord] = {}
        self._lock = threading.Lock()

    def save(self, record: QuarantineRecord) -> None:
        with self._lock:
            self._records[record.quarantine_id] = record

    def get(self, quarantine_id: str) -> Optional[QuarantineRecord]:
        with self._lock:
            return self._records.get(quarantine_id)

    def list(self) -> List[QuarantineRecord]:
        with self._lock:
            return list(self._records.values())

    def count(self) -> int:
        with self._lock:
            return len(self._records)


class FileQuarantineStore(QuarantineStore):
    """Append-only JSONL store. Each line is one serialized QuarantineRecord.

    Note: re-hydration returns dicts wrapped back into QuarantineRecord is not
    performed here to avoid lossy round-tripping; get/list read serialized
    dicts. For the demo and tests the in-memory store is used; this backend is
    provided for durability in real deployments.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not os.path.exists(path):
            open(path, "a").close()

    def save(self, record: QuarantineRecord) -> None:
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _read_all(self) -> List[dict]:
        with self._lock:
            with open(self._path, "r", encoding="utf-8") as fh:
                return [json.loads(ln) for ln in fh if ln.strip()]

    def get(self, quarantine_id: str) -> Optional[QuarantineRecord]:
        for d in self._read_all():
            if d.get("quarantine_id") == quarantine_id:
                return _record_from_dict(d)
        return None

    def list(self) -> List[QuarantineRecord]:
        return [_record_from_dict(d) for d in self._read_all()]

    def count(self) -> int:
        return len(self._read_all())


def _record_from_dict(d: dict) -> QuarantineRecord:
    """Best-effort rehydration of a QuarantineRecord from a stored dict."""
    from ..contracts import Provenance

    prov = Provenance(**d["provenance"])
    rec = QuarantineRecord(
        provenance=prov,
        reason=d["reason"],
        original_payload=d.get("original_payload", {}),
        canonical_snapshot=d.get("canonical_snapshot"),
    )
    rec.quarantine_id = d["quarantine_id"]
    rec.quarantined_at = d["quarantined_at"]
    return rec
