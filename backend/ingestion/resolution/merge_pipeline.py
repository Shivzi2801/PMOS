"""Merge pipeline for PMOS Wave 1 Slice 1.4.

Orchestrates the end-to-end flow for a batch of extracted atoms:

    Extraction -> ACL check -> Resolution -> Provenance -> Canonical Entity

ACL enforcement happens BEFORE resolution/merge so denied evidence can
never influence a canonical entity. Each surviving atom contributes a
ProvenanceRecord, preserving full source lineage on the resolved entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .resolver import ExtractedAtom, Resolver, ResolutionOutcome
from ..errors import ACLViolationError, ProvenanceError
from ..metrics import ResolutionMetrics
from ..models.canonical_entity import CanonicalEntity
from ..models.provenance_record import ProvenanceRecord
from ..models.source_acl import ACLDecision, ACLPrincipal, ACLRegistry


@dataclass
class PipelineInput:
    """An atom paired with the provenance describing its origin."""

    atom: ExtractedAtom
    provenance: ProvenanceRecord


@dataclass
class PipelineResult:
    """Outcome of a full pipeline run."""

    entities: List[CanonicalEntity] = field(default_factory=list)
    provenance: Dict[str, List[ProvenanceRecord]] = field(default_factory=dict)
    rejected: List[str] = field(default_factory=list)  # atom_ids denied by ACL
    metrics: Optional[ResolutionMetrics] = None


class MergePipeline:
    """Drives ACL-gated, provenance-tracked entity resolution."""

    def __init__(
        self,
        acl_registry: ACLRegistry,
        principal: ACLPrincipal,
        resolver: Optional[Resolver] = None,
        metrics: Optional[ResolutionMetrics] = None,
    ) -> None:
        self._acls = acl_registry
        self._principal = principal
        self._resolver = resolver or Resolver()
        self._metrics = metrics or ResolutionMetrics()
        # entity_id -> attached provenance records.
        self._provenance: Dict[str, List[ProvenanceRecord]] = {}

    def _validate_acl(self, item: PipelineInput) -> None:
        """Enforce source visibility before any merge.

        Raises ACLViolationError when the principal may not see the source.
        """
        decision = self._acls.check(item.atom.source_id, self._principal)
        if decision is not ACLDecision.ALLOW:
            raise ACLViolationError(
                f"source {item.atom.source_id} denied for tenant "
                f"{self._principal.tenant_id}",
                source_id=item.atom.source_id,
                tenant_id=self._principal.tenant_id,
            )

    def _validate_provenance(self, item: PipelineInput) -> None:
        """Reject orphan or cross-tenant provenance."""
        prov = item.provenance
        if not prov.source_id:
            raise ProvenanceError(
                "orphan provenance: missing source_id",
                extraction_id=prov.extraction_id,
            )
        if prov.tenant_id != self._principal.tenant_id:
            raise ProvenanceError(
                f"provenance tenant {prov.tenant_id} does not match "
                f"principal tenant {self._principal.tenant_id}",
                extraction_id=prov.extraction_id,
            )

    def process_one(self, item: PipelineInput) -> Optional[ResolutionOutcome]:
        """Process a single atom end-to-end.

        Returns the ResolutionOutcome on success, or None if the atom was
        rejected by ACL enforcement.
        """
        self._metrics.record_processed()

        try:
            self._validate_acl(item)
        except ACLViolationError:
            self._metrics.record_acl_rejection()
            return None

        # Provenance validity is a hard error (orphan provenance) rather
        # than a silent skip — it signals upstream extraction corruption.
        self._validate_provenance(item)

        outcome = self._resolver.resolve(item.atom)
        if outcome.merged:
            self._metrics.record_merged()

        # Attach provenance to the resolved entity.
        self._provenance.setdefault(outcome.entity.entity_id, []).append(
            item.provenance
        )
        self._metrics.record_provenance()

        return outcome

    def run(self, items: List[PipelineInput]) -> PipelineResult:
        """Process a batch and emit canonical entities with lineage."""
        rejected: List[str] = []
        for item in items:
            outcome = self.process_one(item)
            if outcome is None:
                rejected.append(item.atom.atom_id)

        return PipelineResult(
            entities=self._resolver.entities(),
            provenance=dict(self._provenance),
            rejected=rejected,
            metrics=self._metrics,
        )

    @property
    def metrics(self) -> ResolutionMetrics:
        return self._metrics
