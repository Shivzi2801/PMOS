# Contract · Service Interface Standards

**Wave 0 · Slice 2 — standards only.** This file defines the cross-cutting
contracts every PMOS backend service honors: the event envelope, request /
response / error shapes, idempotency, correlation, and tracing. Nothing here is
implemented in this slice; it is the fixed reference that later slices build
against. Where a standard is owned by a platform module, that module is cited:
the BFF (M-05) owns the HTTP wire invariants; the Event Backbone (M-04) owns the
envelope and delivery semantics; Tenancy (M-01) owns context propagation.

The non-negotiable spine: **every interaction is tenant-scoped, idempotent where
it mutates, correlated end-to-end, and traceable.**

---

## 1. Service interface standards

1. **One client surface.** No client calls a service directly. All synchronous
   access is mediated by the BFF (M-05). Services expose internal interfaces
   (event consumers and, later, internal read methods) — never public endpoints.
2. **Two interaction modes only** (per `../README.md` §3): asynchronous events
   (all state changes) and owner-mediated synchronous reads (future, via BFF).
   No service-to-service mutation calls.
3. **Tenant context is ambient and mandatory.** Every operation runs inside a
   resolved `TenantContext` (`organization_id`, `workspace_id`, `user_id`,
   `roles`, `abac`). A request or job without a resolved context is refused
   (`403 tenant_context_unresolved`) — mirrors the Slice-1 rule that the data
   layer refuses context-less queries. Services never read tenant ids from a
   request body or a peer payload; they read them from context.
4. **Versioning is two-key.** Structural version in the URI (`/api/v1`),
   behavioral version via the `PMOS-Version` date header (Stripe-style rolling
   changes; no `/v2` proliferation). Event payloads carry an integer
   `schema_version`. All changes are additive within a major version.
5. **Cursor-only pagination.** No offset/`COUNT(*)`-per-page reads. List
   responses return an opaque `next_cursor`.
6. **`Claim[]` for AI prose (forward-looking).** Any AI-generated prose field
   anywhere on the wire is a `Claim[]` (`{text, citations[], kind, confidence}`),
   never a bare string. Not exercised in this slice (no AI), but reserved so no
   future field regresses to a plain string.
7. **Least privilege at the data layer.** Request-path code runs as the
   RLS-applied role; background workers may run with `BYPASSRLS` but are
   code-review-required to filter `organization_id` explicitly (Slice 1).

---

## 2. Event envelope (owned by M-04)

Every event — document and ingestion alike — is wrapped in this canonical
envelope. The `payload` is the event-specific body defined in
`document-events.md` / `ingestion-events.md`.

| Field | Type | Req | Description |
|---|---|---|---|
| `event_id` | uuid (v7) | yes | Unique per emitted event. **Consumer dedupe key.** |
| `event_type` | string | yes | e.g. `DocumentCreated`, `IngestionRequested`. |
| `aggregate_type` | string | yes | e.g. `document`, `ingestion`. |
| `aggregate_id` | uuid (v7) | yes | The aggregate this event is about. Ordering is per-aggregate. |
| `organization_id` | uuid (v7) | yes | Tenant. Consumers MUST filter on it. |
| `occurred_at` | timestamp | yes | When the fact happened (RFC 3339 UTC). |
| `published_at` | timestamp | no | When the relay published it (set by M-04). |
| `correlation_id` | uuid (v7) | yes | Stable across an entire causal chain (one pipeline traversal). |
| `causation_id` | uuid (v7)\|null | yes | The `event_id` (or request id) that directly caused this event; null for roots. |
| `schema_version` | integer | yes | Major version of `payload`. |
| `payload` | object | yes | Event-specific body. |

**Delivery semantics (M-04).**
- **Outbox invariant:** the event row and the state change commit in one
  transaction. No event without its state change; no state change silently
  without its event.
- **At-least-once** delivery. Consumers are idempotent (dedupe on `event_id`).
- **Per-aggregate ordering** only; no global order. Cross-aggregate causality is
  expressed via `causation_id` + `correlation_id`.
- **Replayable:** events are archived; projections are rebuildable from the
  archive. Replays carry the original `event_id` so idempotent consumers no-op.
- **Tenant-tagged transport:** a consumer fanning into a shared projection must
  filter on `organization_id`; failure to do so is a cross-tenant defect.

---

## 3. Request contracts (future synchronous surface, via BFF)

Applies to the *future* owner-mediated endpoints documented in the service
READMEs. Reserved here so every service implements them identically.

**Standard request headers**

| Header | Req | Purpose |
|---|---|---|
| `Authorization` | yes | Verified JWT (JWKS); resolves `TenantContext`. (Auth itself is M-02, not this slice.) |
| `PMOS-Version` | no | Behavioral version date; defaults to the tenant's pinned version. |
| `Idempotency-Key` | yes on POST | Client-supplied key; see §6. |
| `X-Correlation-Id` | no | Client may supply; otherwise the BFF mints one. See §7. |
| `Content-Type` | yes | `application/json` for commands. |

**Command body rules**
- Tenant ids (`organization_id`, `workspace_id`) are **never** accepted in the
  body; they come from context. A body that includes them is rejected.
- Bodies are validated against a published schema; unknown fields are rejected
  (no silent passthrough).
- All ids are UUIDv7 strings; all timestamps RFC 3339 UTC.

**Read request rules**
- Pagination is cursor-based: `?cursor=<opaque>&limit=<n>` (bounded `limit`).
- Filtering is by explicit, whitelisted query params; never raw SQL fragments.

---

## 4. Response contracts

**Success — single resource**

```json
{
  "data": { "...": "resource fields" },
  "meta": {
    "pmos_version": "2026-06-01",
    "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01"
  }
}
```

**Success — collection (cursor-paginated)**

```json
{
  "data": [ { "...": "item" } ],
  "meta": {
    "pmos_version": "2026-06-01",
    "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
    "next_cursor": "eyJvZmZzZXQiOiJ1dWlkdjctY3Vyc29yIn0",
    "has_more": true
  }
}
```

**Accepted — async job (the universal grammar for anything slow)**

Slow operations (backfills, re-chunk, imports) return `202 Accepted` with a job
resource; progress is observed via the job's stream, and the job can be
cancelled. The job grammar is owned by M-05.

```json
{
  "data": {
    "job_id": "019ec705-0000-7000-8000-000000000j01",
    "status": "accepted",
    "progress": { "pct": 0, "stage": "queued", "detail": null },
    "result_ref": null
  },
  "meta": {
    "pmos_version": "2026-06-01",
    "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01"
  }
}
```

**Response invariants**
- Every response carries `meta.correlation_id` and `meta.pmos_version`.
- Tenant ids are never echoed beyond what the caller is authorized to see; no
  response ever leaks another tenant's data (RLS + envelope discipline).
- Timestamps and ids follow the global conventions (UUIDv7, RFC 3339 UTC).

---

## 5. Error contracts

**One error envelope everywhere** (owned by M-05). No service invents its own
error shape. The envelope never leaks cross-tenant information or sensitive
detail (credentials, raw PII).

```json
{
  "error": {
    "code": "idempotency_conflict",
    "message": "Human-readable, safe summary.",
    "retryable": false,
    "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
    "details": []
  }
}
```

| Field | Type | Description |
|---|---|---|
| `code` | string | Stable, machine-readable error code (snake_case). |
| `message` | string | Safe human-readable summary. No secrets, no PII, no internal stack detail. |
| `retryable` | boolean | Whether the client may retry (with backoff). |
| `correlation_id` | uuid (v7) | Ties the error to the request/trace. |
| `details` | array | Optional field-level validation details (path + reason); never sensitive. |

**Canonical error codes (initial set; additive over time)**

| Code | When |
|---|---|
| `tenant_context_unresolved` | No resolved `TenantContext` (403). |
| `validation_failed` | Body/params failed schema validation (422). |
| `idempotency_conflict` | Same `Idempotency-Key`, different body hash (409). See §6. |
| `not_found` | Resource absent or not visible to this tenant (404). |
| `rate_limited` | Per-class rate limit exceeded (429); `retryable=true`. |
| `conflict` | Optimistic-concurrency or state conflict (409). |
| `forbidden` | Authenticated but not authorized (403). |
| `kill_switch_engaged` | Tenant/connector halted by ops (503); `retryable=true`. |
| `internal_error` | Unexpected server fault (500); `retryable=true`, no detail leaked. |

**Event-side failures** are not HTTP errors; they surface as domain events
(e.g. `IngestionFailed`, `DocumentProcessingCompleted` with `outcome=failed`)
carrying a safe `failure_reason`.

---

## 6. Idempotency rules

1. **Every mutating command carries `Idempotency-Key`.** Mandatory on POST. The
   key is scoped to `(organization_id, user_id, method, path, key)` and cached
   for **24 hours** with the response and a `body_hash` (owned by M-05).
2. **Replay returns the cached response.** A repeat with the same key and the
   same body hash returns the original result — exactly-once *effect* over an
   at-least-once transport.
3. **Conflicting reuse is rejected.** Same key, *different* body hash ⇒
   `409 idempotency_conflict`. Keys are never silently reused for a new payload.
4. **Ingress dedupe is separate and upstream.** `ingestion-service` dedupes
   external source deliveries on `external_event_id` *before* any work, so a
   source redelivering a webhook never double-ingests regardless of HTTP-layer
   idempotency.
5. **Event consumers dedupe on `event_id`.** The transport is at-least-once;
   every consumer must tolerate redelivery (dedupe table or naturally idempotent
   projection write).
6. **Deterministic derived ids.** Chunk identity is deterministic from
   `(document_version_id, chunk_index)` and guarded by `content_hash`, so
   re-chunking the same version never duplicates rows (idempotent by
   construction, not by a key).
7. **Ceremonial/financial writes** (later modules: ledger, meters) bind the
   idempotency key into the audit hash-chain so a replay can never double-append.

---

## 7. Correlation IDs

- **`correlation_id`** identifies one *causal chain* — typically a single piece
  of content's journey: ingestion → screening → document → chunking → indexing.
  It is **minted once** at the root (the receiving webhook/CDC/backfill in
  `ingestion-service`, or the BFF for a client command) and **propagated
  unchanged** onto every downstream event and (future) request in the chain.
- **`causation_id`** identifies the *immediate* cause — the `event_id` (or
  request id) of the thing that directly produced this event. It forms a
  parent-pointer chain; `correlation_id` is the whole-tree label.
- **Client-supplied correlation.** A client may pass `X-Correlation-Id`; if
  absent, the BFF mints one. It is echoed in every response `meta` and every
  error.
- **One ingestion, one thread.** Because the same `correlation_id` rides
  `IngestionRequested → content.ingested → DocumentCreated →
  DocumentProcessingStarted → DocumentProcessingCompleted`, a single content item
  is fully reconstructable across all three services and the downstream pipeline.

---

## 8. Tracing standards

- **OpenTelemetry, end to end.** Every request and every event handler runs in
  an OTel span. `correlation_id` and `causation_id` are recorded as span
  attributes so traces and the event graph reconcile.
- **Trace context propagation.** W3C `traceparent` propagates across the
  synchronous (BFF → service) path; for the asynchronous path, the trace context
  is carried in the event envelope's metadata so a consumer continues the trace
  rather than starting a disconnected one.
- **Tenant-tagged telemetry.** Spans, metrics, and structured logs carry
  `organization_id` (and never raw PII or credentials). Logs are PII-screened
  before they reach any sink.
- **Tail-based sampling.** Sampling decisions are made on completed traces
  (so failures and slow traces are retained) — owned by the observability
  platform (M-09/§15 observability spec), referenced here so service spans are
  emitted in a sampler-compatible way.
- **SLO-bearing spans.** Span names and attributes are stable enough to back
  per-stage SLOs (e.g. ingestion lane latency: live ≤2 min, standard ≤15 min)
  and relay-lag alerts (p99 < 2 min). Services must not rename spans casually;
  span names are part of the operational contract.
- **No silent failures in traces.** A handler that drops or dead-letters an
  event records the outcome on its span and emits the corresponding domain
  event (`IngestionFailed`, processing failure), so a trace never simply ends.

---

*This standards file is the stable center the three services depend on. It
introduces no runtime. Later slices implement the BFF wire layer (M-05), the
outbox/relay (M-04), and per-service internals against these fixed contracts.*
