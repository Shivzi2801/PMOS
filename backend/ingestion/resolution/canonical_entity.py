"""Canonical entity model for PMOS Wave 1 Slice 1.4.

A CanonicalEntity is the deduplicated, resolved representation of one
real-world entity (e.g. an organization, person, or product). Multiple
extracted atoms collapse into a single canonical entity through the
resolution engine.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Centralised so all models share a single clock source, which keeps
    created_at / updated_at comparisons deterministic in tests.
    """
    return datetime.now(timezone.utc)


class EntityIdentityType(str, enum.Enum):
    """The kind of real-world thing a canonical entity represents."""

    ORGANIZATION = "ORGANIZATION"
    PERSON = "PERSON"
    PRODUCT = "PRODUCT"
    LOCATION = "LOCATION"
    UNKNOWN = "UNKNOWN"


@dataclass
class CanonicalEntity:
    """A resolved, deduplicated entity.

    Attributes:
        entity_id: Stable canonical identifier (see resolver.canonical_id).
        identity_type: The semantic type of the entity.
        canonical_name: The preferred display name.
        aliases: All known surface forms that resolve to this entity.
        confidence: Resolution confidence in [0.0, 1.0].
        created_at: First time this entity was materialised.
        updated_at: Last time this entity was mutated (alias add, merge).
    """

    entity_id: str
    identity_type: EntityIdentityType
    canonical_name: str
    aliases: List[str] = field(default_factory=list)
    confidence: float = 1.0
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence!r}"
            )
        # Ensure the canonical name is always discoverable as an alias.
        self._ensure_alias(self.canonical_name)

    def _ensure_alias(self, alias: str) -> None:
        if alias and alias not in self.aliases:
            self.aliases.append(alias)

    def add_alias(self, alias: str) -> bool:
        """Register a new surface form for this entity.

        Returns True if the alias was newly added, False if it already
        existed. Bumps updated_at on a real change.
        """
        if not alias or alias in self.aliases:
            return False
        self.aliases.append(alias)
        self.updated_at = _utcnow()
        return True

    def touch(self) -> None:
        """Mark the entity as mutated."""
        self.updated_at = _utcnow()

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "identity_type": self.identity_type.value,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
