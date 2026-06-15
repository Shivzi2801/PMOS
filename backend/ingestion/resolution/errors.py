"""Error hierarchy for PMOS Wave 1 Slice 1.4.

All slice-specific failures derive from PMOSResolutionBaseError so callers
can catch the whole family or individual conditions.
"""

from __future__ import annotations

from typing import Optional


class PMOSResolutionBaseError(Exception):
    """Base for all entity-resolution slice errors."""


class ResolutionError(PMOSResolutionBaseError):
    """Raised when an atom cannot be resolved to a canonical entity.

    Examples: empty / unnormalisable name, ambiguous match that cannot be
    deterministically disambiguated.
    """

    def __init__(self, message: str, atom_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.atom_id = atom_id


class ACLViolationError(PMOSResolutionBaseError):
    """Raised when a merge is attempted across a denied source.

    Carries the offending source and principal for audit logging.
    """

    def __init__(
        self,
        message: str,
        source_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.source_id = source_id
        self.tenant_id = tenant_id


class ProvenanceError(PMOSResolutionBaseError):
    """Raised for invalid or orphaned provenance.

    Examples: provenance with no source_id, provenance whose tenant does
    not match the resolving principal's tenant.
    """

    def __init__(self, message: str, extraction_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.extraction_id = extraction_id
