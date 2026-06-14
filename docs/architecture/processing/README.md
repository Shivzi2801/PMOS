# PMOS · Document Processing Architecture

**Wave 0 · Slice 3 — architecture and contracts only.**

> **Scope.** This slice defines the complete document-processing architecture
> that will later power AI, RAG, semantic search, retrieval orchestration, and
> agent workflows. It contains **no** application code, APIs, controllers,
> services, workers, queue implementations, embedding code, vector-database
> code, or frontend. Every document here is a blueprint. Anything marked
> *future* is documentation of intent, not an instruction to build now.

This directory is authoritative for **how a document moves from arrival to
retrieval-ready**. It sits on top of two prior slices and must not contradict
them:

- **Slice 1 (database foundation)** — multi-tenant schema, RLS + `FORCE ROW
  LEVEL SECURITY`, UUIDv7 keys, soft-delete, and the load-bearing
  `document_chunks` table: hash-partitioned on `organization_id`, with
  `chunk.id` = the future Qdrant point id, `content_hash` for dedupe, and
  `read_principals` for pre-fusion ACL trim.
- **Slice 2 (service architecture)** — the `document-service`,
  `ingestion-service`, and `chunking-service` boundaries; the canonical event
  envelope (M-04 outbox: `event_id`, `aggregate_id`, `organization_id`,
  `correlation_id`, `causation_id`, `schema_version`); and the interface
  standards (idempotency, error envelope, tracing).

---

## 1. End-to-end processing architecture

PMOS turns hostile, heterogeneous raw content into a **cited, permission-safe,
queryable knowledge base**. The processing path is a **strictly sequential,
event-driven pipeline** where each stage is the sole owner of one transformation
and hands off to the next stage only through events. No stage reaches into
another stage's data.

```
  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │  INGEST  │──▶│  VALIDATE │──▶│ NORMALIZE│──▶│  CHUNK   │──▶│  EMBED   │──▶│  INDEX   │──▶│  READY   │
  │  (M-08)  │   │ + SCREEN  │   │ +ENRICH  │   │ (M-03    │   │ (M-12    │   │ (M-12    │   │ (M-03)   │
  │          │   │  (M-09)   │   │  (M-10)  │   │ chunker) │   │ embed)   │   │  index)  │   │          │
  └──────────┘   └───────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
       │              │               │              │              │              │              │
       ▼              ▼               ▼              ▼              ▼              ▼              ▼
  content.       content.        content.        document.      chunk.         chunk.        Document
  ingested       normalized      enriched        chunked        (embed req)    indexed       Ready
                 / quarantined                                                               (retrievable)

  Every arrow above is an EVENT on the M-04 outbox. Every payload carries
  organization_id + correlation_id so one document is traceable end-to-end.

                              ┌───────────────────────────────┐
                              │   RETRIEVAL  (future, M-13)    │  ◀── reads the READY corpus only
                              │  keyword + semantic + graph    │      (never participates in writes)
                              └───────────────────────────────┘
```

**Source of truth vs. derived.** Postgres is authoritative. `document_versions`
hold immutable content; `document_chunks` are the source of truth for retrieval.
Vectors (Qdrant, future) are **derived and always rebuildable** from
`document_chunks` — nothing is stored in the vector store that cannot be
regenerated. This single rule makes re-embedding, model swaps, and disaster
recovery tractable at millions-of-documents scale.

---

## 2. Processing layers

The pipeline is organized into four layers. Each layer has a distinct failure
domain, scaling profile, and ownership.

| Layer | Stages | Responsibility | Scaling profile |
|---|---|---|---|
| **Ingress layer** | Ingest, Validate, Tenant-verify | Admit content safely; stamp tenant; dedupe on source id. Treat all content as data, never instructions. | Spiky; lane-isolated (live / standard / bulk). |
| **Safety layer** | Screen (PII + prompt-injection), Normalize | Quarantine injection suspects (rendered inert); redact/flag PII; produce clean, typed, normalized content. First layer of injection defense-in-depth. | Classifier-bound; per-lane. |
| **Knowledge-build layer** | Enrich (metadata), Chunk, Embed, Index | Extract metadata; split immutable versions into deterministic chunks; derive vectors; fan out to the index. The longest, highest-volume pole. | Batch-heavy; 10⁸ chunks/large-tenant; partition- and queue-bound. |
| **Retrieval layer** *(future)* | Keyword, Semantic, Graph, Fuse, Rerank, ACL-trim | Answer queries against the READY corpus with tenant + workspace isolation and pre-fusion permission trimming. Read-only with respect to processing. | Latency-bound (`GET /search` <50ms hot path). |

Layers communicate **only** through events. A document's position in the
pipeline is expressed by its **lifecycle state** (see `document-lifecycle.md`)
and governed by the **processing state machine** (see
`processing-state-machine.md`).

---

## 3. Service interaction flow

```
   ingestion-service           document-service            chunking-service         (future) M-12 / M-13
   ─────────────────           ────────────────            ────────────────         ────────────────────
        │                            │                           │                          │
  [receive + verify]                 │                           │                          │
        │  content.ingested ───────► │                           │                          │
        │                      [screen via M-09]                 │                          │
        │                      [persist version]                 │                          │
        │                            │  DocumentCreated ───────►  │                          │
        │                            │  DocumentProcessingStarted │                          │
        │                            │                      [deterministic chunk]            │
        │                            │                            │  document.chunked ─────► │ [embed]
        │                            │                            │                          │  chunk.indexed
        │                            │  ◄── DocumentProcessing ───┤  (reconciled)            │
        │                            │      Completed             │                          │
        │                            │  DocumentReady ──────────────────────────────────────────────────►
        │                            │      (corpus now retrievable; M-13 may read)          │
```

Rules carried forward from Slice 2 (unchanged):

- **Outbox or it didn't happen.** State change and its event commit in one
  transaction.
- **One writer per table; one producer per event type.**
- **At-least-once delivery; idempotent consumers** (dedupe on `event_id`).
- **Per-aggregate ordering only**; cross-aggregate causality via `causation_id`.
- **`correlation_id` is minted once at ingress** and propagated unchanged through
  every downstream event, giving a single end-to-end trace per document.

---

## 4. Processing ownership

| Concern | Owner | Authoritative artifact |
|---|---|---|
| Source admission, dedupe, ACL capture, lanes | `ingestion-service` (M-08) | `ingestion-pipeline.md` |
| PII + injection screening, normalization | screening (M-09, *future*) | referenced; not built this slice |
| Document + immutable versions, hierarchy, lifecycle state, `DocumentReady` | `document-service` (M-03) | `document-lifecycle.md` |
| Metadata extraction / enrichment | extraction (M-10, *future*), with `document-service` owning the persisted fields | `metadata-enrichment.md` |
| Chunk creation, chunk identity, `content_hash`, chunk versioning/replacement | `chunking-service` | `chunking-strategy.md` |
| Embedding generation + bookkeeping write-back, model versioning | index fan-out (M-12, *future*) | `embedding-pipeline.md` |
| Vector/lexical index, point payload, reconciliation | index fan-out (M-12, *future*) | `embedding-pipeline.md` |
| Retrieval, fusion, rerank, pre-fusion ACL trim | retrieval (M-13, *future*) | `retrieval-architecture.md` |
| The state machine that governs all of the above | `document-service` holds the canonical state; each stage reports transitions | `processing-state-machine.md` |
| Canonical processing events | producers per table below | `processing-events.md` |

**The canonical lifecycle state is owned by `document-service`.** Other stages
report progress via events; `document-service` reconciles them into the single
authoritative state for each document so there is exactly one source of truth
for "where is this document in the pipeline."

---

## 5. Future AI integration points

These are reserved seams. None are implemented in this slice; they are placed so
no later design has to re-cut a boundary.

1. **Embedding model boundary (M-07 Model Gateway).** All embedding calls go
   through the Model Gateway under ZDR contracts; the embedding stage names a
   model + dimension and is otherwise model-agnostic. Model swaps are a
   blue/green collection operation (see `embedding-pipeline.md`).
2. **`Claim[]` everywhere AI prose appears.** Any future AI-generated prose field
   is a `Claim[]` (`{text, citations[], kind, confidence}`), never a bare string.
   Retrieval answers cite chunks by `chunk.id`; provenance resolves through the
   chunk → version → document chain.
3. **`read_principals` → pre-fusion ACL trim.** Captured at ingress, propagated
   onto every chunk and (future) vector payload, and force-injected with
   `organization_id` at retrieval time — the second isolation layer mirroring
   RLS. This is the seam every RAG and agent query depends on for
   permission-safety.
4. **Metadata as retrieval filters.** Enriched metadata (product, feature,
   release, team, owner, tags, semantic categories) becomes the structured
   filter surface for retrieval and agent scoping (see `metadata-enrichment.md`).
5. **Retrieval orchestration (M-13).** The READY corpus is the read substrate for
   keyword + semantic + typed-graph retrieval, fusion, rerank, and honest
   abstention ("n sources withheld by permissions"). Agents call retrieval; they
   never touch the processing tables.
6. **Re-processing as a first-class operation.** Re-chunking and re-embedding are
   designed in from the start (content_hash dedupe, deterministic chunk identity,
   blue/green collections) so the corpus can be rebuilt for a new chunking
   strategy or embedding model without downtime — essential at millions of
   documents.

---

## Directory map

```
backend/processing/
├── README.md                      ← this file (authoritative for the pipeline shape)
├── document-lifecycle.md          ← the 10 document states + transitions
├── ingestion-pipeline.md          ← upload → orchestration; success/failure/retry/idempotency
├── chunking-strategy.md           ← fixed/semantic/hybrid/adaptive; chunk metadata, versioning, replacement
├── embedding-pipeline.md          ← embedding flow, ownership, re-embedding, versioning, model swap (blueprint)
├── retrieval-architecture.md      ← keyword/semantic/hybrid/graph, isolation, ranking (future)
├── metadata-enrichment.md         ← metadata extraction strategy, ownership, lifecycle
├── processing-events.md           ← DocumentQueued/Validated/Chunked/Embedded/Indexed/Ready/Failed
└── processing-state-machine.md    ← formal FSM: valid/invalid/recovery/retry transitions, terminal states
```

---

*Slice 3 is architecture only. No runtime, no endpoints, no logic. Later slices
implement each stage against these fixed contracts.*
