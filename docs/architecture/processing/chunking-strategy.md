# Chunking Strategy

**Wave 0 · Slice 3 — architecture only.**

Defines how immutable document versions are split into chunks. Chunks are the
**source of truth for retrieval**; vectors are derived from them. The
`chunking-service` (Slice 2) is the sole writer of `document_chunks` (Slice 1:
hash-partitioned on `organization_id`, `chunk.id` = future Qdrant point id,
`content_hash` for dedupe, `read_principals` for ACL trim). No chunking algorithm
is implemented here — this is the blueprint and the contracts.

---

## Chunking philosophy

Chunking exists to make content **retrievable at the right granularity**: small
enough that a chunk is a focused, citable unit of evidence, large enough to carry
self-contained meaning. Four principles govern every strategy:

1. **Deterministic.** The same version body + same chunking config ⇒ the same
   chunks, the same `chunk_index` ordering, the same `content_hash`. Determinism
   is what makes re-chunking idempotent and chunk identity stable.
2. **Boundary-respecting.** Prefer natural boundaries (headings, paragraphs,
   sentences, code blocks, table rows) over blind character cuts, so a chunk is a
   coherent unit that cites cleanly.
3. **Provenance-preserving.** Every chunk records its position and offsets in the
   source version so retrieval can resolve provenance back to exact location.
4. **Permission-preserving.** Every chunk inherits the version's
   `read_principals` so pre-fusion ACL trim works without re-deriving ACLs.

Chunking is **content-shaping, not interpretation** — it performs no model
inference and never treats content as instructions (safety screening already
happened upstream in M-09).

---

## Strategies

PMOS supports a progression of strategies. Year-1 ships **fixed** and **hybrid**;
**semantic** and **adaptive** are designed-in seams, selected per content type
and per chunking-config version.

### Fixed chunking
Split by a fixed size with overlap, respecting the nearest natural boundary.
Cheap, fully deterministic, predictable cost — the safe default for plain text
and unknown types.

- **Config:** target size (e.g. tokens or characters), overlap, boundary
  preference (sentence/paragraph).
- **Pros:** trivially deterministic, cheap, predictable chunk counts.
- **Cons:** can split a concept across a boundary; ignores structure.

```
  version body ──► [size window + overlap, snapped to sentence end] ──► chunk[0..n]
```

### Semantic chunking *(future seam)*
Split at points of topical change so each chunk is one coherent idea. Uses
lightweight signals (heading structure, embedding-similarity drop between
candidate windows) to place boundaries.

- **Config:** similarity threshold, min/max chunk size guards, structure signals.
- **Pros:** chunks align to meaning → better retrieval precision and cleaner
  citations.
- **Cons:** more expensive; determinism requires pinning the boundary model +
  thresholds in the chunking-config version.

### Hybrid chunking
The Year-1 working strategy: **structure-first, size-bounded**. Split on document
structure (headings → paragraphs → list/table units), then apply fixed
size/overlap *within* a structural unit so no chunk exceeds bounds. Combines the
determinism and cost of fixed with much of the coherence of semantic.

```
  version ──► structural segments (H1/H2/para/code/table)
          ──► within each segment: size-bound + overlap (fixed)
          ──► chunk[0..n] with structural breadcrumb in metadata
```

### Adaptive chunking *(future)*
Per-tenant / per-content-type strategy selection driven by measured retrieval
quality. The system learns which strategy + config yields the best retrieval
outcomes for a given source type and adapts — always behind a pinned, versioned
config so any chunk set remains reproducible. Strictly a future capability; the
seam is the **chunking-config version** recorded on every chunk batch.

---

## Chunk metadata

Each chunk row carries (Slice 1 columns + reserved processing fields):

| Field | Owner | Purpose |
|---|---|---|
| `id` | chunking-service (minted) | Globally unique; **is** the Qdrant point id. Never remapped. |
| `organization_id`, `workspace_id` | stamped from context | RLS partition key + tenant force-injection at retrieval. |
| `document_id`, `document_version_id` | from triggering version | Links chunk → immutable source. |
| `chunk_index` | chunking-service | Deterministic position within the version. |
| `content` | chunking-service | The chunk text (retrieval source of truth). |
| `content_hash` | chunking-service (computed) | Dedupe key governing re-embedding. |
| `read_principals` | propagated from ingress | Pre-fusion ACL trim. |
| `chunking_config_version` *(reserved)* | chunking-service | Which strategy+config produced this chunk (reproducibility/adaptive). |
| `source_offset_start/end` *(reserved)* | chunking-service | Char/token offsets in the version for provenance resolution. |
| `structural_path` *(reserved)* | chunking-service | Breadcrumb (e.g. `H1>H2>para[3]`) for hybrid/semantic citations. |
| `embedded_at`, `embedding_model` | **M-12 (write-back)** | Embedding bookkeeping; chunking leaves null. |
| `created_at`, `updated_at`, `deleted_at` | conventions (Slice 1) | Soft-delete drives tombstoning. |

*(reserved)* fields are named here so the implementation slice adds them as a
single migration; they are not created in this slice.

---

## Chunk ownership

- **`chunking-service` is the sole writer** of `document_chunks` (creation,
  re-chunk, soft-delete/tombstone).
- **`document-service` owns** the `documents` / `document_versions` that are the
  *input*; it never writes chunks.
- **M-12 (future) writes back only** `embedded_at` / `embedding_model` after
  indexing — a single-writer-per-*fact* split (chunking owns the chunk; indexing
  owns the embedding bookkeeping).
- **Vectors are owned by M-12** in the vector store and are derived/rebuildable
  from chunks; no service treats a vector as authoritative.

---

## Chunk versioning

Chunks are tied to an **immutable** `document_version`. There is no in-place edit
of a chunk's content; a content change is always a new version, which yields a
new chunk set.

- A chunk's `(document_version_id, chunk_index)` is its stable logical identity;
  its `id` is its physical identity (and point id).
- A chunk batch records the `chunking_config_version` that produced it, so the
  exact chunks for any version are reproducible and a strategy change is
  explainable.
- Re-chunking a version under the **same** config is a no-op (deterministic +
  `content_hash` guard). Re-chunking under a **new** config produces a new batch;
  the old batch is superseded per the replacement rules below.

---

## Chunk replacement rules

When a new version supersedes an old one, or a re-chunk runs under a new config:

1. **New chunks are written first** (new ids, new batch), then **old chunks are
   soft-deleted** (`deleted_at` set) — never delete-then-write, so retrieval is
   never left with a gap.
2. **Index follows chunks.** `document.chunked` for the new batch drives
   re-embedding + re-index; the old vectors are torn down only after the new ones
   are confirmed indexed and reconciled (no stale-then-empty window).
3. **`content_hash` dedupe across batches:** unchanged chunks (identical hash)
   need not be re-embedded — the new batch can reuse existing vectors where the
   hash matches, minimizing cost on large documents with small edits.
4. **Soft-delete, then purge:** superseded chunks remain tombstoned for the
   retention window (audit + erasure path), then are hard-purged with their
   vectors.
5. **Fail-closed on drift:** a vector whose source chunk is tombstoned or absent
   is **not served** — the reconciler guarantees the index never returns a chunk
   that no longer exists in Postgres.

---

## Examples

> Illustrative only — no implementation. JSON shows the *chunk row contract*,
> not stored format.

**Hybrid chunking of a short PRD section**

Input version body:
```
# Checkout
## One-click pay
Shoppers can save a card for faster checkout. Card data is tokenized; the raw
PAN is never persisted. The wallet integration supports Visa and Mastercard.
```

Resulting chunks (target ~40 tokens, structure-first):
```json
[
  {
    "id": "019ec900-0000-7000-8000-0000000000c1",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "chunk_index": 0,
    "content": "One-click pay. Shoppers can save a card for faster checkout.",
    "content_hash": "sha256:1a2b…",
    "structural_path": "H1[Checkout]>H2[One-click pay]>para[0]",
    "source_offset_start": 11,
    "source_offset_end": 78,
    "read_principals": ["user:acme-pm", "group:acme-product"],
    "chunking_config_version": "hybrid-v1"
  },
  {
    "id": "019ec900-0000-7000-8000-0000000000c2",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "chunk_index": 1,
    "content": "Card data is tokenized; the raw PAN is never persisted. The wallet integration supports Visa and Mastercard.",
    "content_hash": "sha256:3c4d…",
    "structural_path": "H1[Checkout]>H2[One-click pay]>para[0]",
    "source_offset_start": 79,
    "source_offset_end": 196,
    "read_principals": ["user:acme-pm", "group:acme-product"],
    "chunking_config_version": "hybrid-v1"
  }
]
```

**Small edit → minimal re-embedding.** If a new version edits only the second
paragraph, chunk `0`'s `content_hash` is unchanged, so its existing vector is
reused; only chunk `1` (new hash) is re-embedded and re-indexed. The old chunk
`1` is soft-deleted after the new one is confirmed indexed.
