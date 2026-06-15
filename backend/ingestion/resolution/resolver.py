"""Deterministic entity resolution engine for PMOS Wave 1 Slice 1.4.

The resolver maps a stream of extracted atoms onto canonical entities.
Resolution is purely deterministic: the same input set always yields the
same canonical assignment regardless of ordering, because canonical IDs
are derived from normalized text and identity type rather than from
insertion order or wall-clock time.

Matching strategy (in priority order):
  1. exact match      — identical surface name (case-insensitive trim)
  2. alias match      — name appears in an existing entity's alias set
  3. normalized match — normalized form collides with an existing entity

Each successful strategy carries a confidence weight.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..errors import ResolutionError
from ..models.canonical_entity import CanonicalEntity, EntityIdentityType


# Confidence assigned per match strategy. Exact is authoritative; alias and
# normalized are slightly lower to reflect surface-form transformation.
CONFIDENCE_EXACT = 1.0
CONFIDENCE_ALIAS = 0.95
CONFIDENCE_NORMALIZED = 0.9
CONFIDENCE_NEW = 1.0

# Common organizational suffixes stripped during normalization so that
# "Acme Corp", "ACME Corporation", and "Acme" collapse together.
_ORG_SUFFIXES = {
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "co",
    "company",
    "plc",
    "gmbh",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass
class ExtractedAtom:
    """An extraction emitted upstream and fed into resolution.

    Attributes:
        atom_id: Unique id of this extraction atom.
        name: The raw surface form as extracted.
        identity_type: The entity type this atom asserts.
        source_id: Source the atom came from (for ACL + provenance).
    """

    atom_id: str
    name: str
    identity_type: EntityIdentityType
    source_id: str


def normalize_text(raw: str) -> str:
    """Produce a deterministic normalized key for a surface form.

    Steps: lowercase, strip, drop punctuation, collapse whitespace, and
    remove trailing organizational suffixes. Empty results are invalid and
    signal an unresolvable atom to the caller.
    """
    if raw is None:
        return ""
    lowered = raw.strip().lower()
    # Replace any run of non-alphanumeric chars with a single space.
    cleaned = _NON_ALNUM.sub(" ", lowered).strip()
    if not cleaned:
        return ""
    tokens = cleaned.split()
    # Strip trailing org suffixes (handles "acme corp" -> "acme").
    while len(tokens) > 1 and tokens[-1] in _ORG_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def canonical_id(identity_type: EntityIdentityType, normalized: str) -> str:
    """Generate a stable canonical ID from type + normalized text.

    The ID is a deterministic hash so the same logical entity always lands
    on the same ID across runs, processes, and machines.
    """
    basis = f"{identity_type.value}:{normalized}".encode("utf-8")
    digest = hashlib.sha256(basis).hexdigest()[:24]
    return f"ent_{digest}"


@dataclass
class ResolutionOutcome:
    """Result of resolving a single atom."""

    entity: CanonicalEntity
    created: bool        # True if a brand-new entity was minted
    merged: bool         # True if the atom folded into an existing entity
    strategy: str        # "exact" | "alias" | "normalized" | "new"
    confidence: float


class Resolver:
    """Deterministic resolver maintaining an in-run canonical index."""

    def __init__(self) -> None:
        # normalized-key -> CanonicalEntity (per identity type namespace).
        self._by_key: Dict[Tuple[str, str], CanonicalEntity] = {}
        # exact surface form (lowercased) -> CanonicalEntity
        self._by_exact: Dict[Tuple[str, str], CanonicalEntity] = {}
        # alias surface form (lowercased) -> CanonicalEntity
        self._by_alias: Dict[Tuple[str, str], CanonicalEntity] = {}

    def _ns(self, atom: ExtractedAtom) -> str:
        return atom.identity_type.value

    def resolve(self, atom: ExtractedAtom) -> ResolutionOutcome:
        """Resolve a single atom to a canonical entity.

        Raises:
            ResolutionError: if the atom name cannot be normalized.
        """
        if not atom.name or not atom.name.strip():
            raise ResolutionError("atom has empty name", atom_id=atom.atom_id)

        normalized = normalize_text(atom.name)
        if not normalized:
            raise ResolutionError(
                f"name {atom.name!r} normalizes to empty", atom_id=atom.atom_id
            )

        ns = self._ns(atom)
        surface = atom.name.strip().lower()

        # 1. Exact match.
        existing = self._by_exact.get((ns, surface))
        if existing is not None:
            return self._merge_into(existing, atom, "exact", CONFIDENCE_EXACT)

        # 2. Alias match.
        existing = self._by_alias.get((ns, surface))
        if existing is not None:
            return self._merge_into(existing, atom, "alias", CONFIDENCE_ALIAS)

        # 3. Normalized match.
        existing = self._by_key.get((ns, normalized))
        if existing is not None:
            return self._merge_into(
                existing, atom, "normalized", CONFIDENCE_NORMALIZED
            )

        # 4. No match -> mint a new canonical entity.
        return self._mint(atom, normalized, ns)

    def _merge_into(
        self,
        entity: CanonicalEntity,
        atom: ExtractedAtom,
        strategy: str,
        confidence: float,
    ) -> ResolutionOutcome:
        ns = self._ns(atom)
        surface = atom.name.strip().lower()
        if entity.add_alias(atom.name.strip()):
            self._by_alias[(ns, surface)] = entity
        # Confidence is the min of existing and this match (conservative).
        entity.confidence = min(entity.confidence, confidence)
        entity.touch()
        return ResolutionOutcome(
            entity=entity,
            created=False,
            merged=True,
            strategy=strategy,
            confidence=confidence,
        )

    def _mint(
        self, atom: ExtractedAtom, normalized: str, ns: str
    ) -> ResolutionOutcome:
        eid = canonical_id(atom.identity_type, normalized)
        entity = CanonicalEntity(
            entity_id=eid,
            identity_type=atom.identity_type,
            canonical_name=atom.name.strip(),
            confidence=CONFIDENCE_NEW,
        )
        surface = atom.name.strip().lower()
        self._by_key[(ns, normalized)] = entity
        self._by_exact[(ns, surface)] = entity
        self._by_alias[(ns, surface)] = entity
        return ResolutionOutcome(
            entity=entity,
            created=True,
            merged=False,
            strategy="new",
            confidence=CONFIDENCE_NEW,
        )

    def entities(self) -> List[CanonicalEntity]:
        """Return all canonical entities currently materialised."""
        # Deduplicate by id since one entity is referenced from many maps.
        seen: Dict[str, CanonicalEntity] = {}
        for entity in self._by_key.values():
            seen[entity.entity_id] = entity
        return list(seen.values())
