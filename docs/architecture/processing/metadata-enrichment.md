# Metadata Enrichment

**Wave 0 · Slice 3 — architecture only.**

Defines how documents and chunks acquire the structured metadata that powers
retrieval filtering, agent scoping, and the product-hierarchy linkage. Metadata
turns an undifferentiated chunk pile into a **filterable, navigable knowledge
base** — essential for retrieval precision at millions of documents.

Two kinds of metadata, captured at different stages:

- **Intrinsic metadata** — what the source already knows, captured at **ingress**
  by `ingestion-service` (source ids, author, timestamps, filename/title, content
  type, and the source `read_principals`).
- **Derived metadata** — inferred during the **enrichment** stage (extraction,
  M-10, future) and/or linked by `document-service`: product, feature, release,
  team, owner, tags, keywords, semantic categories.

This file is the strategy + ownership + lifecycle blueprint. No extractor or
classifier is implemented here.

---

## Metadata extraction strategy

Enrichment runs **after screening/normalization and before or alongside
chunking**, so derived metadata can be attached to the document and propagated to
its chunks. The strategy is **layered, cheap-first**, to control cost:

1. **Structural extraction (free).** Parse titles, headings, sections, source
   fields — deterministic, no model.
2. **Rule/lookup linkage (cheap).** Map source identifiers to PMOS hierarchy
   (e.g. a Jira project → a `product`/`feature`) via tenant-configured mappings.
3. **Classifier inference (mid-tier model, only when needed).** Derive semantic
   categories, keywords, and topical tags via small-tier models through the Model
   Gateway (M-07) — applied selectively (cheap-first cascade means most content
   never needs the expensive path).
4. **Human/agent confirmation (optional).** High-value links (product/feature
   assignment) can be human- or agent-confirmed; confirmed links carry higher
   trust than inferred ones.

Each metadata value records **how it was derived** (`source: intrinsic |
rule | inferred | confirmed`) and a confidence, so retrieval can weight or filter
on trust and agents can reason about provenance.

---

## Metadata examples

| Field | Kind | Typical source | Use in retrieval / agents |
|---|---|---|---|
| **Product** | derived (linkage) | source project → hierarchy mapping; confirmation | Scope a query/agent to a product line. |
| **Feature** | derived (linkage) | source epic/label → `features` row | Pull all evidence under a feature. |
| **Release** | derived (linkage/structural) | version/fix-version field; roadmap link | Filter to "current release" context. |
| **Team** | derived (rule/linkage) | source group/space → team mapping | Restrict agent context to a team's surface. |
| **Owner** | intrinsic/derived | source author / assignee | Attribute and route; filter "owned by". |
| **Tags** | derived (rule + inferred) | labels + classifier | Faceted filtering; topical grouping. |
| **Keywords** | derived (inferred) | keyword extraction | Boost keyword-retrieval recall. |
| **Semantic categories** | derived (inferred) | classifier (small-tier model) | Coarse semantic filtering before vector search; agent task routing. |

Intrinsic fields also carried but not "enriched": `doc_type`, source system,
external id, created/updated timestamps, content type, and `read_principals`
(security-load-bearing, captured at ingress, never inferred).

---

## Ownership

| Metadata concern | Owner |
|---|---|
| Intrinsic capture at ingress (incl. `read_principals`) | `ingestion-service` (M-08) |
| Derivation (structural, rule, classifier inference) | extraction (M-10, future) |
| Hierarchy linkage (product/feature/release/team) | `document-service` (M-03) writes the linkage; extraction proposes it |
| Persisted document/chunk metadata fields | `document-service` (document-level), `chunking-service` (chunk-level propagation) |
| Tenant-configured mapping rules (source → hierarchy/team) | configuration owned per tenant (admin surface, future) |
| Classifier model access | Model Gateway (M-07, future) |

**Rule:** metadata that affects **security** (`read_principals`) is captured at
the source and never inferred or widened. Metadata that affects **retrieval
relevance** (tags, categories, keywords) may be inferred but always carries its
derivation + confidence.

---

## Lifecycle

```
  ingress ──► intrinsic metadata captured (incl. read_principals)
          ──► enrichment: structural ─► rule/linkage ─► inferred ─► (optional confirm)
          ──► document-level metadata persisted (documents) 
          ──► propagated to chunks at chunk time (chunking-service)
          ──► becomes retrieval filter surface + vector payload (M-12)
          ──► re-evaluated on re-processing (new version / new config / model swap)
```

1. **Capture (ingress).** Intrinsic fields + source ACL stamped onto the
   forthcoming version.
2. **Derive (enrichment).** Layered extraction attaches derived fields to the
   document with `source` + `confidence`.
3. **Persist + link.** `document-service` persists document-level metadata and
   writes hierarchy linkage (product/feature/release/team).
4. **Propagate to chunks.** At chunk time, retrieval-relevant metadata (and
   always `read_principals` + `organization_id`/`workspace_id`) is carried onto
   each chunk and, downstream, into the vector payload (M-12) so it is filterable
   without a join at query time.
5. **Re-evaluate on re-processing.** A new version, a new chunking config, or a
   model swap re-runs enrichment; confirmed links are preserved, inferred fields
   are recomputed.
6. **Tombstone on delete.** Metadata follows the document's soft-delete/purge
   lifecycle; deleted documents' metadata is not served.

---

## Metadata as a contract

- Metadata fields used as retrieval filters are **typed and whitelisted** (no
  free-form filter injection at the boundary).
- Metadata payloads are **schema-versioned** (additive-first), so retrieval and
  agents can rely on field stability across pipeline evolution.
- Every derived field records `{value, source, confidence, derived_at}` so the
  system can audit, weight, and explain why a document matched — feeding the
  `Claim[]` provenance model.

> No extraction, classification, or linkage code is implemented in this slice.
> This is the contract M-10 and the enrichment stage build against.
