# ingestion-service

**Maps to:** M-08 · Connectors & Ingestion Ingress
**Wave 0 · Slice 2 — blueprint only.** No connectors, no webhooks, no queue, no
logic here. This document fixes the boundary, the lifecycle, and the events.

The **ingress** of the Knowledge Platform: it receives content from external
source systems, normalizes it into a tenant-stamped envelope, captures the
source ACL, routes it onto the correct priority lane, and announces it to the
pipeline. It is the first stage of a strictly sequential pipeline
(ingest → screen → extract → resolve → index → retrieve).

---

## 1. Responsibilities

- Own **connector registration and lifecycle**: which sources a tenant has
  connected, their health, and their coverage estimate. Connector *credentials*
  live only in an external secret store and are referenced by handle — **never**
  stored in PMOS tables, never returned by any API (Threat #3).
- **Receive ingress** from source systems via webhooks (HMAC-verified, deduped
  on `external_event_id`) and via CDC polling for sources without webhooks.
- **Normalize** each item into a `RawContentEnvelope` (uniform, tenant-stamped,
  lane-tagged) and **capture the source `read_principals`** at ingress so
  downstream retrieval can trim by permission.
- **Route by lane**: live (≤2 min), standard (≤15 min), bulk (best-effort).
  Backfills ride the bulk lane and **must never delay live detection**.
- **Announce** ingested content to the pipeline via `content.ingested`
  (per envelope, lane-tagged) and surface the request/started/completed/failed
  lifecycle via the canonical ingestion events.
- Treat **all ingested content as data, never instructions** — it is handed to
  screening (M-09) before anything else in the platform interprets it.
- Be the **single writer** of its tables and the **single publisher** of its
  events.

**Explicitly NOT responsible for** (owned elsewhere): PII / prompt-injection
screening (→ M-09), persisting documents (→ `document-service`), chunking
(→ `chunking-service`), embedding / indexing (→ M-12), retrieval (→ M-13),
secret storage (→ external secret manager).

---

## 2. Ingestion lifecycle

A single unit of ingestion moves through these states. Each transition emits a
canonical event (see `contracts/ingestion-events.md`).

```
   REQUESTED ──► STARTED ──► (normalize + ACL capture + lane route) ──► COMPLETED
       │            │                                                      │
       │            └────────────────────► FAILED ◄───────────────────────┘
       │                                     ▲
       └─────────────────────────────────────┘  (terminal failure at any point)
```

| State | Meaning | Event emitted |
|---|---|---|
| **REQUESTED** | An ingestion was asked for (webhook received & verified, backfill enqueued, or CDC poll found new data). Deduped on `external_event_id`. | `IngestionRequested` |
| **STARTED** | Work has begun on this ingestion unit on its assigned lane. | `IngestionStarted` |
| **COMPLETED** | Content normalized, ACL captured, envelope emitted to the pipeline. | `IngestionCompleted` (+ `content.ingested` per envelope) |
| **FAILED** | Terminal failure after the retry policy is exhausted, or a non-retryable error (e.g. bad HMAC, deauthorized connector). | `IngestionFailed` |

Each ingestion unit is identified by an `ingestion_id` (UUIDv7) and carries the
`external_event_id` for source-side idempotency, `organization_id`, the `lane`,
and a `correlation_id` that follows the content through the entire pipeline.

---

## 3. Workflow orchestration

Orchestration is **event-driven and lane-aware**, not a synchronous call chain.
The design (implementation deferred to a later slice) is:

- **Receivers** (webhook receiver, CDC poller, backfill enqueuer) are the only
  entry points. Each produces a deduped `IngestionRequested` via the outbox.
- A **lane router** assigns the unit to live / standard / bulk based on source
  type and request kind (a live webhook → live lane; a backfill → bulk lane).
- **Per-lane workers** pick up requested units, transition them to STARTED,
  perform normalization + ACL capture, and on success emit `content.ingested`
  (the pipeline hand-off) and `IngestionCompleted`.
- **Lane isolation is a hard guarantee**: the bulk lane can never starve or
  delay the live lane. Backfills are explicitly best-effort.
- Orchestration state lives in the ingestion-owned tables; there is **no
  cross-service synchronous orchestration**. Downstream stages react to
  `content.ingested` on their own schedule.

> No workflow engine, no queue, and no worker code are produced in this slice.
> The orchestration model is specified so the seam (events + lanes) is fixed.

---

## 4. Retry strategy

Retries are **bounded, idempotent, and lane-respecting**. The contract (to be
implemented later):

1. **Idempotency first.** Every ingress is deduped on `external_event_id`
   *before* any work, so a source redelivering a webhook never double-ingests.
   Retries therefore cannot create duplicates.
2. **Classify the error.**
   - *Retryable* (transient source 5xx, network timeout, secret-store
     hiccup, rate-limit 429) → retry with **exponential backoff + jitter**.
   - *Non-retryable* (HMAC verification failure, deauthorized connector,
     malformed payload, tenant kill-switch engaged) → fail fast, emit
     `IngestionFailed` with a terminal reason, no retry.
3. **Bounded attempts.** A maximum attempt count per lane (live lanes fail
   faster to protect freshness; bulk lanes tolerate more attempts). On
   exhaustion → `IngestionFailed`.
4. **Dead-letter, not data loss.** Exhausted units are recorded in an ingestion
   dead-letter state for manual inspection/replay; the raw envelope is retained
   (subject to retention policy) so replay is possible.
5. **Backfills are restartable.** A `BackfillJob` checkpoints progress so a
   failed backfill resumes rather than restarting, and never blocks the live
   lane.
6. **Kill-switch aware.** If a tenant/connector kill-switch is engaged
   (`ops.killswitch_engaged`), in-flight retries stop and units park until the
   switch clears.

Retry counts, backoff parameters, and per-lane attempt ceilings are
configuration to be set in the implementation slice; they are intentionally not
hard-coded here.

---

## 5. Event publishing model

- **Outbox-bound.** Every ingestion event and every `content.ingested` envelope
  is written through the M-04 transactional outbox in the same transaction as
  the ingestion-state write. There is no out-of-band publish.
- **At-least-once delivery; idempotent consumers.** Downstream (screening, etc.)
  must dedupe on `event_id`. Producers guarantee no *duplicate ingestion* via
  `external_event_id`, but the event transport itself is at-least-once.
- **Lane-tagged.** `content.ingested` and the lifecycle events carry the `lane`
  so downstream preserves freshness discipline.
- **Tenant-stamped + correlated.** Every event carries `organization_id`,
  `correlation_id` (stable for the whole pipeline traversal of this content),
  and `causation_id` (the event/command that caused this one).
- **Two streams, one lifecycle.** The lifecycle events
  (`IngestionRequested/Started/Completed/Failed`) describe the *ingestion unit*;
  `content.ingested` describes the *content payload* handed to the pipeline. A
  single COMPLETED ingestion may emit one `IngestionCompleted` and one-or-more
  `content.ingested` envelopes (e.g. a batch).

Schemas and examples: `contracts/ingestion-events.md`. Envelope, correlation,
and tracing standards: `contracts/service-interfaces.md`.

---

## 6. Future APIs (documentation only — not built in this slice)

All owner-mediated through the BFF (M-05); commands carry `Idempotency-Key`;
webhook endpoints are HMAC-verified; **no credential is ever returned**.

| Method & path (future) | Purpose |
|---|---|
| `POST /api/v1/connectors` | Register a connector (credential goes to secret store; only a handle persists). |
| `GET /api/v1/connectors` / `GET /api/v1/connectors/{id}` | List / read connectors (never returns secrets). |
| `DELETE /api/v1/connectors/{id}` | Deauthorize a connector (emits `connector.deauthorized`). |
| `GET /api/v1/connectors/{id}/health` | Connector health (healthy / degraded / down). |
| `GET /api/v1/connectors/{id}/coverage` | Coverage estimate + honest upgrade framing. |
| `POST /api/v1/connectors/{id}/backfill` | Enqueue a bulk-lane backfill (async job). |
| `POST /api/v1/connectors/oauth/callback` | OAuth authorization callback. |
| `POST /api/v1/webhooks/connectors/{provider}` | HMAC-verified, deduped webhook ingress (Critical API). |
| `GET /api/v1/ingestion/{id}` | Read ingestion-unit status (lifecycle state). |

No endpoint, receiver, poller, or worker is implemented in Slice 2.
