"""Knowledge atom contracts for the F-10 Extraction Engine.

This module defines the structured knowledge atoms produced by the extraction
pipeline. Only ``FactAtom`` is implemented in Slice 1.3. The base class and
``AtomType`` enum are designed so that future atom types — EntityAtom,
RelationshipAtom, SignalAtom, EventAtom — can be added without modifying the
pipeline, ranking, or confidence layers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Tuple


class AtomType(str, Enum):
    """Enumeration of all knowledge atom types.

    Only ``FACT`` is produced in Slice 1.3. The remaining members are declared
    as forward-looking extension points and MUST NOT be emitted yet.
    """

    FACT = "fact"
    # --- Reserved for future slices (do not emit in S1.3) ---
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    SIGNAL = "signal"
    EVENT = "event"


def _new_atom_id() -> str:
    return f"atom_{uuid.uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SourceOffset:
    """Character span within the source document that produced an atom.

    Offsets are half-open ``[start, end)`` indices into the canonical
    document's text content.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < 0:
            raise ValueError("SourceOffset indices must be non-negative")
        if self.end < self.start:
            raise ValueError("SourceOffset end must be >= start")

    def as_tuple(self) -> Tuple[int, int]:
        return (self.start, self.end)

    def to_dict(self) -> Dict[str, int]:
        return {"start": self.start, "end": self.end}


@dataclass
class Atom:
    """Base class for all knowledge atoms.

    Concrete subclasses define their semantic payload. Common provenance and
    scoring metadata live here so the pipeline, confidence engine, and ranker
    can operate polymorphically across atom types.
    """

    tenantId: str
    documentId: str
    confidence: float
    sourceDocumentId: str
    sourceOffsets: Tuple[SourceOffset, ...] = field(default_factory=tuple)
    atomId: str = field(default_factory=_new_atom_id)
    extractedAt: datetime = field(default_factory=_utc_now)

    # Set by subclasses. Not a dataclass field to keep subclass field ordering clean.
    atom_type: AtomType = field(init=False, default=AtomType.FACT)

    def dedup_key(self) -> Tuple[Any, ...]:
        """Key used by the ranker to detect duplicate/equivalent atoms.

        Subclasses MUST override this to express semantic equivalence
        independent of confidence, provenance, or atom identity.
        """
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["atom_type"] = self.atom_type.value
        data["extractedAt"] = self.extractedAt.isoformat()
        data["sourceOffsets"] = [o.to_dict() for o in self.sourceOffsets]
        return data


@dataclass
class FactAtom(Atom):
    """A subject-predicate-object fact extracted from a document.

    Example: ("Acme Corp", "upgraded_to", "Enterprise Plan").
    """

    subject: str = ""
    predicate: str = ""
    object: str = ""

    def __post_init__(self) -> None:
        object.__setattr__  # no-op reference to silence linters on frozen base
        self.atom_type = AtomType.FACT
        if not self.subject or not self.predicate or not self.object:
            raise ValueError(
                "FactAtom requires non-empty subject, predicate, and object"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0.0, 1.0]")

    def _norm(self, value: str) -> str:
        return " ".join(value.lower().split())

    def dedup_key(self) -> Tuple[Any, ...]:
        return (
            self.tenantId,
            self.documentId,
            AtomType.FACT.value,
            self._norm(self.subject),
            self._norm(self.predicate),
            self._norm(self.object),
        )
