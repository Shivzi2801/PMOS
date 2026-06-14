# Embedding Pipeline

**Wave 0 · Slice 3 — architecture and contracts only. No embedding code.**

Defines how chunks become vectors. Embeddings are **derived and always
rebuildable** from `document_chunks` (Slice 1) — nothing in the vector store
that cannot be regenerated. All model access goes through the **Model Gateway
(M-07)** under ZDR contracts. The index fan-out stage (**M-12**, future) owns
this pipeline; `chunking-service` produces its input and M-12 writes back only
embedding bookkeeping onto the chunk row.

---

## Embedding generation flow

```
  document.chunked (from chunking-service)
        │
        ▼
  ┌────────────────────┐   skip if content_hash already mapped to a live vector
  │  Dedup gate         │   (cost control at millions-of-chunks scale)
  └─────────┬───────────┘
            ▼
  ┌────────────────────┐   batch chunks onto the BATCH lane (interactive work
  │  Batch scheduler    │   is never starved by bulk embedding)
  └─────────┬───────────┘
            ▼
  ┌────────────────────┐   Model Gateway (M-07): embed under ZDR; tiered routing;
  │  Embedder           │   model + dimension are explicit and fixed per collection
  └─────────┬───────────┘
            ▼
  ┌────────────────────┐   int8 quantization (cost/latency), payload assembly:
  │  Vector builder     │   {id = chunk.id, dense, sparse, organization_id,
  └─────────┬───────────┘    read_principals, metadata}
            ▼
  ┌────────────────────┐   upsert into the dimension-fixed collection (blue/green
  │  Index upsert (M-12)│   on model change); confirm; then write back bookkeeping
  └─────────┬───────────┘
            ▼
  DocumentEmbedded → DocumentIndexed → (reconcile) → DocumentReady
```

Key properties:

- **`content_hash` dedupe** prevents redundant re-embedding of unchanged chunks.
- **Batch lane** isolates embedding from interactive load; embedding is
  best-effort throughput, not interactive latency.
- **Model + dimension are explicit.** A collection is dimension-fixed; a chunk is
  never embedded into a collection of a different dimension.
- **`id` = chunk id = point id.** No mapping table, end to end.
- **Payload carries isolation + ACL.** `organization_id` (force-injected) and
  `read_principals` ride into the vector payload for tenant filtering and
  pre-fusion ACL trim (the second isolation layer mirroring RLS).

---

## Embedding ownership

| Concern | Owner |
|---|---|
| Triggering event (`document.chunked`) | `chunking-service` (producer) |
| Embedding generation, batching, quantization | index fan-out (M-12, future) |
| Model access + ZDR enforcement + tiered routing | Model Gateway (M-07, future) |
| Vector store (collections, points, payloads) | M-12 (Qdrant, future) |
| Bookkeeping write-back (`embedded_at`, `embedding_model`) onto chunk row | M-12 |
| Chunk content + `content_hash` (the input + dedupe key) | `chunking-service` |
| Source of truth that vectors are rebuilt from | `document_chunks` (Postgres, M-03) |

**Single-writer-per-fact** holds across the chunk row: chunking owns the chunk;
M-12 owns the embedding columns. No other service writes either.

---

## Re-embedding strategy

Re-embedding is a first-class, designed-in operation (essential at scale):

1. **Triggers:** a new chunk batch (content changed), an embedding-model change,
   a dimension change, a quality regression on a tenant, or index drift detected
   by the reconciler.
2. **Dedupe-aware:** only chunks whose `content_hash` lacks a live vector for the
   target model are embedded; unchanged chunks reuse their existing vectors.
3. **Batch-laned:** large re-embeddings ride the bulk/batch lane and never affect
   interactive retrieval or live ingestion.
4. **Blue/green for model/dimension changes:** a new dimension means a **new
   collection**; the corpus is re-embedded into it in the background, then traffic
   is swapped atomically (see model replacement below). The old collection is
   retired only after parity is confirmed.
5. **Rebuild from source:** because chunks are the source of truth, the entire
   vector index can be reconstructed from `document_chunks` at any time — this is
   also the disaster-recovery path.
6. **Reconciliation:** a nightly reconciler compares Postgres `document_chunks`
   against the index and heals drift (missing/extra/stale points), fail-closed.

---

## Versioning strategy

- **Embedding-model version** is recorded per chunk (`embedding_model`) and per
  collection (a collection is bound to one model + dimension).
- **Vectors are versioned by collection**, not in-row: a model change produces a
  new collection (new version), never a mixed collection.
- **`content_hash`** versions the *content* of a chunk; together
  `(content_hash, embedding_model)` uniquely determines whether a vector needs
  (re)generation.
- **Chunking-config version** (from `chunking-strategy.md`) versions the *shape*
  of chunks; a config change yields a new chunk batch, which yields new vectors.
- **Schema-versioned payloads:** the vector point payload carries a
  `schema_version` so payload fields (metadata, ACL) can evolve additively.

This three-axis versioning — **content** (`content_hash`), **shape**
(`chunking_config_version`), **model** (`embedding_model`/collection) — lets PMOS
reason precisely about exactly which vectors must be rebuilt for any change,
which is what keeps re-processing affordable at millions of documents.

---

## Model replacement strategy

Swapping the embedding model is a **gated, blue/green, zero-downtime** operation:

```
  current: collection_A  (model M1, dim D1)  ◀── all retrieval reads here
                │
   (1) provision collection_B (model M2, dim D2)  [green, no traffic]
   (2) background re-embed corpus from document_chunks into collection_B
       (batch lane; content_hash dedupe where model-compatible)
   (3) shadow/canary retrieval reads against collection_B; compare quality
   (4) reconcile parity Postgres↔collection_B
   (5) atomic swap: retrieval reads collection_B  ◀── now live
   (6) retire collection_A after a safety window (rollback target until then)
```

Guarantees:

- **No mixed-dimension collection** — D1 and D2 never coexist in one collection.
- **Reads never see a half-migrated corpus** — the swap is atomic at the
  collection pointer.
- **Rollback is immediate** — collection_A remains until the safety window
  closes, so a regression reverts by pointing back.
- **Driven by `embedding.model_changed`** (from the Model Gateway, M-07) which
  the index fan-out consumes to begin the blue/green build.
- **Gated by quality** — the swap only proceeds after canary/shadow comparison
  meets the retrieval-quality bar (release-gated, per the platform's eval
  discipline).

> No embeddings, model calls, vector-store operations, or collection management
> are implemented in this slice. This file is the contract the implementation
> slice (M-12) builds against.
