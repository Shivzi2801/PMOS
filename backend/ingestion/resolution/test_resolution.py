"""Tests for PMOS Wave 1 Slice 1.4 entity resolution.

Covers:
  - duplicate entities merge
  - ACL blocks merge
  - provenance retained
  - canonical IDs stable
plus supporting unit tests for normalization and metrics.
"""

from __future__ import annotations

import pytest

from pmos.s1_4.engine.merge_pipeline import (
    MergePipeline,
    PipelineInput,
)
from pmos.s1_4.engine.resolver import (
    ExtractedAtom,
    Resolver,
    canonical_id,
    normalize_text,
)
from pmos.s1_4.errors import ProvenanceError, ResolutionError
from pmos.s1_4.metrics import ResolutionMetrics
from pmos.s1_4.models.canonical_entity import EntityIdentityType
from pmos.s1_4.models.provenance_record import (
    ConnectorType,
    ProvenanceRecord,
    SourceType,
)
from pmos.s1_4.models.source_acl import (
    ACLDecision,
    ACLPrincipal,
    ACLRegistry,
    SourceACL,
)


TENANT = "tenant-a"


def _atom(atom_id: str, name: str, source_id: str = "src-1") -> ExtractedAtom:
    return ExtractedAtom(
        atom_id=atom_id,
        name=name,
        identity_type=EntityIdentityType.ORGANIZATION,
        source_id=source_id,
    )


def _prov(extraction_id: str, source_id: str = "src-1", tenant: str = TENANT) -> ProvenanceRecord:
    return ProvenanceRecord(
        source_id=source_id,
        source_type=SourceType.DOCUMENT,
        document_id="doc-1",
        connector_type=ConnectorType.GDRIVE,
        connection_id="conn-1",
        tenant_id=tenant,
        extraction_id=extraction_id,
        evidence_text="found in body",
    )


def _registry(*, allow_source: str = "src-1", tenant: str = TENANT) -> ACLRegistry:
    reg = ACLRegistry()
    reg.register(SourceACL(source_id=allow_source, tenant_id=tenant,
                           default_decision=ACLDecision.ALLOW))
    return reg


# --------------------------------------------------------------------------
# Normalization & canonical ID
# --------------------------------------------------------------------------

def test_normalize_collapses_org_variants():
    assert normalize_text("Acme Corp") == "acme"
    assert normalize_text("ACME Corporation") == "acme"
    assert normalize_text("Acme") == "acme"


def test_canonical_id_stable_and_deterministic():
    a = canonical_id(EntityIdentityType.ORGANIZATION, "acme")
    b = canonical_id(EntityIdentityType.ORGANIZATION, "acme")
    assert a == b
    assert a.startswith("ent_")


def test_empty_name_raises_resolution_error():
    r = Resolver()
    with pytest.raises(ResolutionError):
        r.resolve(_atom("x", "   "))


# --------------------------------------------------------------------------
# Duplicate entities merge
# --------------------------------------------------------------------------

def test_duplicate_entities_merge():
    reg = _registry()
    pipe = MergePipeline(reg, ACLPrincipal(tenant_id=TENANT))
    items = [
        PipelineInput(_atom("a1", "Acme Corp"), _prov("a1")),
        PipelineInput(_atom("a2", "ACME Corporation"), _prov("a2")),
        PipelineInput(_atom("a3", "Acme"), _prov("a3")),
    ]
    result = pipe.run(items)

    assert len(result.entities) == 1
    entity = result.entities[0]
    assert {"Acme Corp", "ACME Corporation", "Acme"}.issubset(set(entity.aliases))
    assert pipe.metrics.entities_processed == 3
    assert pipe.metrics.entities_merged == 2  # two folded into the first
    assert pipe.metrics.merge_rate == pytest.approx(2 / 3)


def test_canonical_ids_stable_across_runs():
    """Same inputs in different order yield the same canonical id."""
    reg = _registry()
    p1 = MergePipeline(reg, ACLPrincipal(tenant_id=TENANT))
    r1 = p1.run([
        PipelineInput(_atom("a1", "Acme Corp"), _prov("a1")),
        PipelineInput(_atom("a2", "Acme"), _prov("a2")),
    ])
    p2 = MergePipeline(_registry(), ACLPrincipal(tenant_id=TENANT))
    r2 = p2.run([
        PipelineInput(_atom("b1", "Acme"), _prov("b1")),
        PipelineInput(_atom("b2", "Acme Corp"), _prov("b2")),
    ])
    assert r1.entities[0].entity_id == r2.entities[0].entity_id


# --------------------------------------------------------------------------
# ACL blocks merge
# --------------------------------------------------------------------------

def test_acl_denies_source_blocks_merge():
    reg = ACLRegistry()
    reg.register(SourceACL("src-allow", TENANT, default_decision=ACLDecision.ALLOW))
    reg.register(SourceACL("src-deny", TENANT, default_decision=ACLDecision.DENY))
    pipe = MergePipeline(reg, ACLPrincipal(tenant_id=TENANT))

    items = [
        PipelineInput(_atom("a1", "Acme", source_id="src-allow"),
                      _prov("a1", source_id="src-allow")),
        PipelineInput(_atom("a2", "Acme", source_id="src-deny"),
                      _prov("a2", source_id="src-deny")),
    ]
    result = pipe.run(items)

    assert "a2" in result.rejected
    assert pipe.metrics.acl_rejections == 1
    # Denied atom contributed no provenance.
    entity = result.entities[0]
    assert len(result.provenance[entity.entity_id]) == 1


def test_unknown_source_fails_closed():
    reg = ACLRegistry()  # nothing registered
    pipe = MergePipeline(reg, ACLPrincipal(tenant_id=TENANT))
    result = pipe.run([PipelineInput(_atom("a1", "Acme"), _prov("a1"))])
    assert result.rejected == ["a1"]
    assert pipe.metrics.acl_rejections == 1


def test_cross_tenant_access_denied():
    reg = _registry(tenant=TENANT)
    pipe = MergePipeline(reg, ACLPrincipal(tenant_id="tenant-b"))
    result = pipe.run([PipelineInput(_atom("a1", "Acme"), _prov("a1", tenant=TENANT))])
    assert result.rejected == ["a1"]


# --------------------------------------------------------------------------
# Provenance retained
# --------------------------------------------------------------------------

def test_provenance_retained_on_entity():
    reg = _registry()
    pipe = MergePipeline(reg, ACLPrincipal(tenant_id=TENANT))
    result = pipe.run([
        PipelineInput(_atom("a1", "Acme Corp"), _prov("a1")),
        PipelineInput(_atom("a2", "Acme"), _prov("a2")),
    ])
    entity = result.entities[0]
    records = result.provenance[entity.entity_id]
    assert len(records) == 2
    assert {r.extraction_id for r in records} == {"a1", "a2"}
    assert pipe.metrics.provenance_records_created == 2


def test_orphan_provenance_raises():
    with pytest.raises(ValueError):
        # source_id empty is rejected at construction.
        _prov("a1", source_id="")


def test_cross_tenant_provenance_raises():
    reg = _registry()
    pipe = MergePipeline(reg, ACLPrincipal(tenant_id=TENANT))
    bad = PipelineInput(_atom("a1", "Acme"), _prov("a1", tenant="other"))
    with pytest.raises(ProvenanceError):
        pipe.process_one(bad)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def test_metrics_snapshot_shape():
    m = ResolutionMetrics()
    m.record_processed()
    m.record_merged()
    m.record_provenance()
    snap = m.snapshot()
    assert set(snap) == {
        "entities_processed",
        "entities_merged",
        "merge_rate",
        "acl_rejections",
        "provenance_records_created",
    }


def test_merge_rate_zero_when_empty():
    assert ResolutionMetrics().merge_rate == 0.0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
