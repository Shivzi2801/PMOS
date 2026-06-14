# chunking-service

**Maps to:** M-03 · Core Persistence (the chunker) → produces the M-12 input
**Wave 0 · Slice 2 — blueprint only.** No chunking algorithm, no embedding, no
vector calls, no queue, no logic here. Boundary and lifecycle only.

The **sole writer of `document_chunks`** — the Postgres↔Qdrant contract table
from Slice 1. It turns an immutable document version into a deterministic set of
chunks, stamps each chunk with the metadata the rest of the pipeline depends on,
and hands them off to the (future) index fan-out. Chunks are the **source of
truth for retrieval**; vectors are derived from them and always rebuildable.

---

## 1. Responsibilities

- React to a new immutable **document version** and produce its **chunks**
  deterministically (same version body + same chunking config ⇒ same chunks,
  same ids-by-position, same `content_hash`).
- Be the **single writer** of `document_chunks`, including the partitioned,
  high-write lifecycle (hash-partitioned on `organization_id`, Slice 1).
- **Mint `document_chunks.id`** — which *is* the future Qdrant point id. No
  mapping table ever exists; the id this service assigns is consumed verbatim
  downstream.
- Compute and store the per-chunk **`content_hash`** so redundant re-embedding
  is avoided downstream (unchanged chunk ⇒ no re-embed).
- Carry forward the **`read_principals`** captured at ingress onto each chunk so
  retrieval can trim by permission *pre-fusion*.
- Emit **`document.chunked`** to hand the chunk set to index fan-out (M-12,
  future), and the **processing lifecycle** signals back to `document-service`.
- Handle **re-chunking** when a new version supersedes an old one, and
  **tombstoning** when a document is deleted — never silent divergence between
  Postgres and the derived index.

**Explicitly NOT responsible for** (owned elsewhere): persisting documents /
versions (→ `document-service`), screening (→ M-09), embedding generation or
Qdrant upsert (→ M-12), retrieval / rerank / ACL-trim at query time (→ M-13).

> **Chunking is content-shaping, not interpretation.** It does not read content
> as instructions and performs no model inference. PII/injection screening
> happens upstream (M-09) in the full pipeline; this service shapes already
> admissible content into chunks.

---

## 2. Chunking ownership

- **Owns the `document_chunks` table outright** as sole writer. `document-service`
  owns the `documents` / `document_versions` that are its *input*; this service
  owns the chunks that are the *output*.
- **Owns the chunking strategy/configuration** (chunk size, overlap, boundary
  rules) as a versioned concern. The strategy version is recorded so a re-chunk
  under a new strategy is explainable and reproducible.
- **Owns the chunk id contract.** Because `document_chunks` is hash-partitioned
  on `organization_id`, the real primary key is composite `(id, organization_id)`
  (Slice 1); `id` alone remains the globally-unique point id. This service is the
  only place that contract is created.
- **Does not own** the vectors (Qdrant, derived, owned by M-12) nor the
  retrieval read path.

---

## 3. Chunk lifecycle

```
   document.version_created ──► CHUNKING ──► chunks written (document_chunks)
            (from doc-service)      │                 │
                                    │                 └─► document.chunked  (→ index fan-out, future)
                                    │                 └─► DocumentProcessingCompleted (→ doc-service)
                                    └─► DocumentProcessingStarted (→ doc-service)

   document.version superseded ──► RE-CHUNK new version; old chunks soft-deleted/tombstoned
   DocumentDeleted             ──► tombstone all chunks for the document (deleted_at), emit teardown signal
```

| Phase | What happens | Signal |
|---|---|---|
| **Triggered** | A new immutable version is announced. | consumes `document.version_created` |
| **Started** | Chunking begins for that version. | emits `DocumentProcessingStarted` (relayed via doc-service semantics) |
| **Written** | Chunks persisted to `document_chunks` with ids, `content_hash`, `chunk_index`, `read_principals`, version linkage. | (table write) |
| **Handed off** | Chunk set announced to the index pipeline. | emits `document.chunked` |
| **Completed** | Processing for this version is done. | emits `DocumentProcessingCompleted` |
| **Re-chunk** | A newer version supersedes; superseded chunks are soft-deleted and the new set written. | `document.chunked` for new version |
| **Tombstone** | Document soft-deleted upstream; all its chunks set `deleted_at`; downstream index told to tear down. | reacts to `DocumentDeleted` |

**Idempotency.** Re-delivery of `document.version_created` for an
already-chunked version is a no-op: chunk identity is deterministic from
`(document_version_id, chunk_index)` and guarded by `content_hash`. Chunking the
same version twice never duplicates rows.

---

## 4. Metadata ownership

Per chunk, this service writes and owns (columns from Slice 1's
`document_chunks`):

| Column | Owned/maintained by chunking-service | Notes |
|---|---|---|
| `id` | **Minted here** | = future Qdrant point id; never remapped. |
| `organization_id`, `workspace_id` | Stamped from context | RLS partition key; never from payload. |
| `document_id`, `document_version_id` | Set from the triggering version | Links chunk → its immutable source. |
| `chunk_index` | **Set here** | Deterministic position within the version. |
| `content` | **Set here** | The chunk text (source of truth for retrieval). |
| `content_hash` | **Computed here** | Dedupe key governing downstream re-embedding. |
| `read_principals` | Carried forward | Captured at ingress (M-08); chunking propagates it. |
| `embedded_at`, `embedding_model` | **Reserved, written by M-12 later** | Embedding bookkeeping; chunking leaves null. |
| `created_at`, `updated_at`, `deleted_at` | Conventions (Slice 1) | Soft-delete drives tombstoning. |

The `embedded_at` / `embedding_model` columns are the **integration point** to
the future embedding stage: chunking owns the row, M-12 stamps the embedding
bookkeeping after it indexes. This split keeps a single writer per *fact* even
though two services touch the row across its lifetime.

---

## 5. Future embedding integration points (documentation only)

The hand-off to embedding/index fan-out (M-12) is via **event + the chunk row
contract**, never a synchronous call:

1. **`document.chunked`** is the trigger. Its payload references the
   `document_version_id` and the chunk-id range / batch produced (schema in
   `contracts/document-events.md` — note: `document.chunked` is a hierarchy/
   processing event published by the document side of the pipeline; chunking
   emits it as the producer of chunks). M-12's `embedding-worker` consumes it.
2. **`content_hash` dedupe.** M-12 skips re-embedding any chunk whose
   `content_hash` already maps to an existing vector — chunking guarantees the
   hash is stable for identical content.
3. **`id` = point id.** M-12 upserts a Qdrant point whose id is exactly the
   chunk `id`. No translation layer.
4. **`read_principals` in the payload.** M-12 copies `read_principals` (and
   `organization_id`) into the Qdrant point payload for pre-fusion ACL trim and
   tenant force-injection (enforcement layer #2, mirroring RLS).
5. **`embedded_at` / `embedding_model` write-back.** After successful index,
   M-12 stamps these columns on the chunk row; chunking never writes them.
6. **Rebuildability.** Because chunks are the source of truth, the entire vector
   index is reconstructible from `document_chunks` at any time — the nightly
   reconciler (M-12) heals drift against this table.

No embedding code, model call, or Qdrant interaction exists in this slice.

---

## 6. Future APIs (documentation only — not built in this slice)

Chunking is primarily an **internal, event-driven worker** with **no
client-facing write API** — clients never create chunks directly; chunks are a
derived consequence of a committed document version. The only future surfaces
are operational/diagnostic, owner-mediated through the BFF:

| Method & path (future) | Purpose |
|---|---|
| `GET /api/v1/documents/{id}/chunks` | Diagnostic read of a document's chunks (cursor-paginated, owner-mediated). |
| `POST /api/v1/documents/{id}/rechunk` | Ops/admin: force a re-chunk under the current strategy version (async job, idempotent). |
| `GET /api/v1/ingestion/chunking/{versionId}/status` | Processing status for a version's chunking. |

Retrieval/search over chunks is **not** a chunking-service API — that is M-13
(`GET /api/v1/search`) and is out of scope here. Request/response/error,
idempotency, correlation, and tracing standards are defined in
`contracts/service-interfaces.md`.
