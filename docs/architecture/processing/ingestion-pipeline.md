# Ingestion Pipeline

**Wave 0 · Slice 3 — architecture only.**

Defines how content enters PMOS and is orchestrated into the processing
pipeline. Builds on Slice 2's `ingestion-service` (M-08) blueprint and the
ingestion event contract (`IngestionRequested/Started/Completed/Failed`,
`content.ingested`). No receivers, pollers, queues, or workers are implemented
here.

Two ingress origins converge on the same pipeline:

1. **Connector ingress** — webhooks / CDC from external sources (Zendesk, Jira,
   Notion, Confluence, Slack, Linear; Gong/Salesforce as coverage upgrades).
2. **In-product upload** — a user or agent commits content directly (e.g. an
   uploaded file or an authored document).

Both produce an immutable `document_version` and enter the lifecycle at
**Uploaded** → **Queued**.

---

## Upload flow

```
   ┌──────────────────┐
   │ Document received │  webhook (HMAC-verified) | CDC poll | in-product upload
   └────────┬──────────┘
            ▼
   ┌──────────────────┐   external_event_id dedupe (connector ingress)
   │   Validation      │   size/type/schema; reject malformed early
   └────────┬──────────┘
            ▼
   ┌──────────────────┐   resolve TenantContext; stamp organization_id + workspace_id
   │ Tenant verification│   refuse if unresolved (security-critical)
   └────────┬──────────┘
            ▼
   ┌──────────────────┐   capture source read_principals; extract intrinsic metadata
   │ Metadata extraction│   (filename, source ids, author, timestamps, source ACL)
   └────────┬──────────┘
            ▼
   ┌──────────────────┐   assign lane (live ≤2m / standard ≤15m / bulk best-effort)
   │ Queueing strategy │   commit version + outbox event in one tx
   └────────┬──────────┘
            ▼
   ┌──────────────────┐   DocumentQueued → pipeline picks up by lane
   │ Processing        │   (screen → normalize → chunk → embed → index → ready)
   │ orchestration     │
   └───────────────────┘
```

### Document received
Entry point. For connector ingress, a webhook is **HMAC-verified** and deduped on
`external_event_id` before any work (an unverified webhook reaching the pipeline
is a security failure). For in-product upload, the request is authenticated and
tenant-scoped by the BFF. Emits `IngestionRequested` (after dedupe) with a freshly
minted `correlation_id`.

### Validation
Structural admission: content size within bounds, type supported, payload
well-formed against the source's expected schema. Validation is **cheap and
fail-fast** — malformed input never consumes downstream capacity. On success the
content is admissible (it is still treated as data, not instructions; semantic
safety screening happens later in M-09).

### Tenant verification
Resolve the `TenantContext` and stamp `organization_id` + `workspace_id` onto the
forthcoming version. A request without a resolved context is **refused**
(mirrors Slice 1's data-layer refusal and Slice 2's `tenant_context_unresolved`).
Tenant ids are never read from the payload body; they come from context. This is
the existential isolation boundary — it cannot be skipped.

### Metadata extraction
Capture intrinsic metadata available at ingress: source system ids, author,
created/updated timestamps, filename/title, content type, and — critically — the
**source `read_principals`** (the source system's ACL), which will travel onto
every chunk for pre-fusion ACL trim. Richer semantic metadata (product, feature,
team, tags, categories) is derived later in the enrichment stage
(`metadata-enrichment.md`); this step captures only what the source already knows.

### Queueing strategy
Assign a **priority lane** and enqueue:

- **Live (≤2 min):** interactive/just-changed content (a live webhook).
- **Standard (≤15 min):** routine updates.
- **Bulk (best-effort):** backfills and large imports.

Lane isolation is a hard guarantee: **bulk never delays live**. The version row
and the outbox event (`DocumentQueued`) are committed in a single transaction
(the M-04 outbox invariant). Backfills checkpoint progress so they are
restartable and never block the live lane.

### Processing orchestration
Once `DocumentQueued` is emitted, orchestration is **event-driven, not a
synchronous call chain**. Per-lane workers (future) advance the document through
screening → normalization → chunking → embedding → indexing → ready, each stage
reacting to the prior stage's event. The ingestion service's job ends at handing
clean, tenant-stamped, lane-tagged content to the pipeline; it does not drive
downstream stages.

---

## Success path

```
 received ─► validated ─► tenant-verified ─► metadata captured ─► version committed
          ─► DocumentQueued ─► (screen ─► normalize ─► chunk ─► embed ─► index)
          ─► reconciled ─► DocumentReady
```

Events emitted along the happy path (lifecycle markers; full schemas in
`processing-events.md`): `IngestionRequested → IngestionStarted →
content.ingested → IngestionCompleted` (ingestion side) then `DocumentQueued →
DocumentValidated → DocumentChunked → DocumentEmbedded → DocumentIndexed →
DocumentReady` (processing side). All share the originating `correlation_id`.

---

## Failure path

Failures are **classified, surfaced as events, and never silent**.

| Stage | Failure | Classification | Outcome |
|---|---|---|---|
| Received | Bad HMAC / unauthenticated | non-retryable | reject; `IngestionFailed(hmac_invalid)`; no pipeline entry |
| Received | Duplicate `external_event_id` | n/a | dedupe no-op (success, not failure) |
| Validation | Malformed / unsupported type / too large | non-retryable | `IngestionFailed(malformed_payload)` |
| Tenant verify | Context unresolved | non-retryable | refuse; security event; `IngestionFailed` |
| Tenant verify | Kill-switch engaged | retryable-later | park; resume when cleared |
| Metadata | Source ACL unavailable | degraded | proceed with honest ACL-fidelity flag (never widen access) |
| Queueing | Outbox write fails | retryable | transaction rolls back; nothing half-committed |
| Downstream | Screening/chunk/embed/index terminal error | varies | document → **Failed**; `DocumentFailed` event with stage + reason |

A failure at any processing stage drives the document to the **Failed** lifecycle
state and emits `DocumentFailed` (see `processing-events.md`) with the stage,
reason, and whether it was dead-lettered. The raw envelope is retained (subject
to retention) so the unit is replayable.

---

## Retry path

Retries are **bounded, idempotent, and lane-aware** (carried forward from Slice
2's retry strategy):

1. **Classify:** transient (5xx, timeout, 429, provider outage) → retry with
   **exponential backoff + jitter**; permanent (bad HMAC, malformed, deauthorized,
   unsupported) → fail fast, no retry.
2. **Bound attempts per lane:** live lanes fail faster to protect freshness; bulk
   lanes tolerate more attempts. On exhaustion → **Failed** + dead-letter.
3. **Dead-letter, don't lose:** exhausted units are parked for inspection/replay;
   raw content retained.
4. **Recover from Failed:** an operator or auto-policy can re-queue a Failed
   document (`Failed → Queued`), re-entering the pipeline from the start of the
   failed stage. Because every stage is idempotent, re-entry never duplicates.
5. **Kill-switch aware:** retries halt while a tenant/connector kill-switch is
   engaged and resume when cleared.

---

## Idempotency behavior

Idempotency is layered so that **at-least-once delivery never produces
duplicate effects**:

1. **Source dedupe (ingress):** connector ingress is deduped on
   `external_event_id` *before* any work — a source redelivering a webhook never
   double-ingests.
2. **Content dedupe (`content_hash`):** an identical body (same hash, same
   document) does not schedule redundant processing; re-embedding is skipped when
   the chunk's `content_hash` already maps to a vector.
3. **Command idempotency (`Idempotency-Key`):** in-product uploads carry the
   mandatory `Idempotency-Key` (Slice 2 §6), scoped `(org, user, method, path,
   key)`, 24h cache; replay returns the cached result, conflicting reuse →
   `409 idempotency_conflict`.
4. **Deterministic chunk identity:** chunk ids derive from
   `(document_version_id, chunk_index)` — re-chunking the same version never
   duplicates rows.
5. **Event-consumer dedupe (`event_id`):** every downstream consumer dedupes on
   the envelope `event_id`, tolerating redelivery and replay.
6. **Idempotent state transitions:** re-applying a transition that has already
   occurred (e.g. `DocumentChunked` redelivered) is a no-op; the state machine
   rejects illegal repeats and absorbs legal ones.

Together these guarantee **exactly-once effect over an at-least-once transport**,
which is the property required to safely process millions of documents with
retries and replays.
