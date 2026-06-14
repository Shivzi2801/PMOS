# Document Lifecycle

**Wave 0 · Slice 3 — architecture only.**

This document defines the canonical states a document moves through from arrival
to retrieval-ready, and every legal transition between them. The **state is
owned by `document-service`** (Slice 2); other stages report progress via events
and `document-service` reconciles them into the single authoritative state. The
formal machine — including invalid, recovery, retry, and terminal transitions —
is in `processing-state-machine.md`; this file is the human-readable definition
of each state.

Each document carries its current state, the timestamp it entered that state,
and a `correlation_id` tying it to its originating ingestion. State is persisted
on the `documents` row (a `processing_state` column is reserved for the
implementation slice; not created here).

---

## State overview

```
  Draft ─► Uploaded ─► Queued ─► Processing ─► Chunked ─► Embedded ─► Indexed ─► Ready
    │         │          │           │            │          │           │         │
    │         └──────────┴───────────┴────────────┴──────────┴───────────┴─────────┤
    │                          (any active state may fail)                          │
    │                                                                               ▼
    │                                                                          [Failed]*
    │                                                                               │
    └───────────────────────────────► Deleted ◄────────────────────── Archived ◄───┘
                                       (terminal)                       (from Ready)

  * Failed is a recoverable holding state, not strictly terminal — see below.
```

---

## 1. Draft

**Description.** The document exists as a handle but has no committed content
yet. Used for in-product authoring (e.g. a PRD being written) before a first
immutable version is committed. Not all documents pass through Draft — content
ingested from a connector is born at **Uploaded**.

**Entry criteria.**
- A `documents` row is created with no `current_version_id`.
- Originates from an in-product create action (future API), never from ingestion.

**Exit criteria.**
- First immutable version committed → **Uploaded** (or directly to **Queued** if
  the create action requests immediate processing).

**Failure modes.**
- Abandoned drafts (no version ever committed): swept by a retention policy to
  **Deleted** after an inactivity window. Not a processing failure.

---

## 2. Uploaded

**Description.** Content has arrived and an immutable `document_version` exists,
but processing has not yet been scheduled. This is the convergence point for both
ingestion-sourced content (`content.ingested` → screened → persisted) and
in-product committed drafts.

**Entry criteria.**
- An immutable `document_version` is committed with a `content_hash`.
- Tenant context (`organization_id`, `workspace_id`) is stamped; for
  ingestion-sourced content, `read_principals` were captured at ingress.

**Exit criteria.**
- Admitted to the processing queue → **Queued** (emits `DocumentQueued`).

**Failure modes.**
- Version committed but tenant context unresolved → rejected before persistence
  (cannot reach Uploaded; surfaces as an ingestion/validation failure).
- Duplicate content (same `content_hash`, same document) → de-duplicated; no new
  processing scheduled (idempotency).

---

## 3. Queued

**Description.** The document version is admitted to the processing pipeline and
is waiting for a worker on its priority lane (live / standard / bulk). Queueing
preserves lane discipline: backfills (bulk) never delay live content.

**Entry criteria.**
- `DocumentQueued` emitted; the version is enqueued on its lane.
- The document is not under a tenant/connector kill-switch.

**Exit criteria.**
- A worker picks it up and validation begins → **Processing** (the
  `DocumentProcessingStarted` signal from Slice 2 marks this).

**Failure modes.**
- Queue backpressure beyond SLO → alerting; the document waits (still Queued),
  it does not fail.
- Kill-switch engaged while queued → parked; resumes when cleared.
- Poison message (repeatedly un-processable) → **Failed** after the retry policy
  (see `processing-state-machine.md`).

---

## 4. Processing

**Description.** Active enrichment is under way: validation, screening (PII +
prompt-injection), normalization, and metadata extraction. This is the
"in-flight" umbrella state before chunking. Content is treated strictly as data.

**Entry criteria.**
- A worker has claimed the version; `DocumentValidated` is emitted once
  validation + tenant verification pass.
- Screening verdict obtained (clean, or quarantined-and-inert).

**Exit criteria.**
- Content normalized, metadata extracted, screening cleared → ready to chunk →
  **Chunked** is the next state once chunks are written.
- Quarantined (injection suspect) → rendered inert; does **not** proceed to
  Chunked; surfaces as a held/failed state pending manual release.

**Failure modes.**
- Validation failure (malformed, unsupported type, schema violation) → **Failed**.
- Tenant verification failure → **Failed** (security event; never silently
  dropped).
- Injection suspected → quarantined; held, not embedded, not indexed.
- Classifier/model unavailable → retryable; remains Processing under backoff.

---

## 5. Chunked

**Description.** The immutable version has been split into deterministic chunks
written to `document_chunks`. Chunks are the source of truth for retrieval. Each
chunk has its `id` (the future Qdrant point id), `chunk_index`, `content_hash`,
and propagated `read_principals`.

**Entry criteria.**
- `chunking-service` has written all chunks for the version.
- `DocumentChunked` emitted with the chunk count and id range/batch.

**Exit criteria.**
- Chunks dispatched to embedding → **Embedded** once vectors are generated.

**Failure modes.**
- Partial chunk write (crash mid-batch) → idempotent re-chunk completes the set
  (chunk identity is deterministic from `(document_version_id, chunk_index)`); no
  duplicates.
- Empty/again-unchunkable content → **Failed** with a terminal reason.

---

## 6. Embedded

**Description.** Vectors have been generated for the version's chunks (via the
Model Gateway, future M-12) and the chunk rows' embedding bookkeeping
(`embedded_at`, `embedding_model`) has been written back. Vectors are derived and
rebuildable; chunks remain the source of truth.

**Entry criteria.**
- Embeddings produced for all (non-deduped) chunks of the version.
- `DocumentEmbedded` emitted with the embedding model + dimension.

**Exit criteria.**
- Vectors upserted into the index → **Indexed**.

**Failure modes.**
- Embedding provider outage / quota → retryable; remains Embedded-pending under
  backoff (batch lane), never silently degraded.
- `content_hash` already embedded → skipped (dedupe); not a failure.
- Dimension mismatch (model changed mid-flight) → routed to the blue/green
  collection for the new model; never mixed into a fixed-dimension collection.

---

## 7. Indexed

**Description.** The version's vectors (and lexical/sparse representations) are
upserted into the vector/lexical index with their payloads
(`organization_id`, `read_principals`, metadata). The document is now physically
searchable but not yet declared retrieval-ready.

**Entry criteria.**
- Index upsert confirmed for all chunks of the version.
- `DocumentIndexed` emitted.

**Exit criteria.**
- Reconciliation confirms Postgres↔index parity for the version → **Ready**
  (emits `DocumentReady`).

**Failure modes.**
- Index upsert partial failure → reconciler heals drift; remains Indexed-pending
  until parity holds.
- Index unavailable → retryable.

---

## 8. Ready

**Description.** The terminal *success* state of processing. The document is
fully chunked, embedded, indexed, reconciled, and retrievable by M-13. This is
the only state in which a document contributes to retrieval results.

**Entry criteria.**
- `DocumentReady` emitted after Postgres↔index parity is confirmed.
- `read_principals` present on all chunks (or honestly flagged where the source
  exposes no ACL).

**Exit criteria.**
- A new version supersedes it → the *new version* re-enters the pipeline at
  **Queued**; the old version's chunks/vectors are superseded (see chunk
  replacement rules in `chunking-strategy.md`).
- Archived by policy or action → **Archived**.
- Deleted → **Deleted**.

**Failure modes.**
- Post-Ready index drift detected by the reconciler → repaired in place; if
  unrepairable, the version is re-queued for re-index (returns to an active
  state) rather than serving stale results.

---

## 9. Archived

**Description.** The document is retained for audit/history but excluded from
active retrieval. Its content and chunks are preserved; its vectors may be
evicted from the hot index to save cost (rebuildable on demand from chunks).

**Entry criteria.**
- Reached from **Ready** by an explicit archive action or a retention policy.
- Excluded from retrieval result sets (filtered out, not deleted).

**Exit criteria.**
- Restored → re-index from chunks → **Ready** (no re-chunk needed if content
  unchanged; vectors rebuilt).
- Deleted → **Deleted**.

**Failure modes.**
- Restore requested but chunks missing (drift) → re-chunk from the immutable
  version, then re-embed/re-index.

---

## 10. Deleted

**Description.** Terminal. The document is soft-deleted (`deleted_at` set, Slice
1 convention). Derived data is **tombstoned**, never silently dropped; a hard
purge is a separate, audited 30-day process (the GDPR erasure path).

**Entry criteria.**
- A `DocumentDeleted` event (Slice 2) is emitted; `deleted_at` is set on the
  document, its versions, and its chunks; the index is told to tear down the
  corresponding points.

**Exit criteria.**
- None from soft-delete except hard **purge** after the retention window
  (removes content + chunks + vectors permanently, leaving audit tombstones).

**Failure modes.**
- Index teardown incomplete → reconciler ensures no deleted document's vectors
  remain queryable (fail-closed: a vector whose source chunk is tombstoned is
  not served).

---

## Complete state transitions

| From | To | Trigger | Kind |
|---|---|---|---|
| (none) | Draft | In-product create (no version) | normal |
| (none) | Uploaded | Version committed from ingestion/screening | normal |
| Draft | Uploaded | First immutable version committed | normal |
| Draft | Deleted | Abandoned-draft retention sweep | normal |
| Uploaded | Queued | Admitted to pipeline (`DocumentQueued`) | normal |
| Queued | Processing | Worker claims; validation begins | normal |
| Queued | Failed | Poison message / retries exhausted | failure |
| Processing | Chunked | Screened + normalized + chunks written (`DocumentChunked`) | normal |
| Processing | Failed | Validation / tenant-verify failure; injection quarantine | failure |
| Chunked | Embedded | Vectors generated (`DocumentEmbedded`) | normal |
| Chunked | Failed | Unchunkable / empty terminal | failure |
| Embedded | Indexed | Vectors upserted (`DocumentIndexed`) | normal |
| Embedded | Failed | Embedding terminal failure | failure |
| Indexed | Ready | Postgres↔index parity confirmed (`DocumentReady`) | normal |
| Indexed | Failed | Index terminal failure | failure |
| Ready | Queued | New version supersedes (new version re-enters) | normal (re-processing) |
| Ready | Archived | Archive action / retention | normal |
| Ready | Deleted | Delete action (`DocumentDeleted`) | normal |
| Archived | Ready | Restore (re-index from chunks) | recovery |
| Archived | Deleted | Delete action | normal |
| Failed | Queued | Operator/auto retry (re-enter pipeline) | retry/recovery |
| Failed | Deleted | Give up / delete | normal |
| any active | Failed | Unrecoverable error in that stage | failure |
| any non-terminal | Deleted | Delete action | normal |

**State invariants.**
- A document is retrievable **only** in **Ready**.
- Tenant + workspace isolation holds in every state (RLS, Slice 1) — a failed or
  archived document is never visible cross-tenant.
- Every transition emits a processing event (`processing-events.md`) carrying the
  document's `correlation_id`, so the whole journey is one trace.
- **Failed** is a *recoverable* holding state: it can return to **Queued** (retry)
  or proceed to **Deleted**. **Deleted (purged)** and a fully **Archived-then-
  purged** document are the truly terminal ends.

The formal machine, including explicitly *invalid* transitions and the
retry/recovery edges, is specified in `processing-state-machine.md`.
