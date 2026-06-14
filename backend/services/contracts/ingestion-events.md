# Contract · Ingestion Events

**Wave 0 · Slice 2 — canonical event definitions.** Wire contracts only.
Versioned, additive-first. Delivered via the M-04 transactional outbox with
at-least-once semantics; consumers must dedupe on `event_id`. Producers
guarantee no duplicate *ingestion* via `external_event_id`.

These four events describe the lifecycle of a single **ingestion unit**
(identified by `ingestion_id`). They are distinct from `content.ingested`, which
carries the *content payload* into the screening stage — a single completed
ingestion may emit one `IngestionCompleted` plus one-or-more `content.ingested`
envelopes (e.g. a batch). All events use the canonical envelope from
`service-interfaces.md`.

**Common payload conventions**
- `ingestion_id` (UUIDv7) identifies the ingestion unit; it is the `aggregate_id`.
- `external_event_id` is the source-system idempotency key; ingress is deduped
  on it before any work.
- `lane` ∈ `{live, standard, bulk}` and is preserved through the pipeline.
- `connector_id` references the connector; **no credential ever appears** in any
  payload.
- Timestamps are RFC 3339 UTC; ids are UUIDv7 strings; `schema_version` starts at 1.
- `organization_id` is present and authoritative for tenant filtering.

---

## IngestionRequested

**Purpose.** Record that an ingestion was asked for — a verified webhook was
received, a backfill was enqueued, or a CDC poll discovered new data. This is
the entry state; it is emitted *after* `external_event_id` dedupe so it never
represents a duplicate.

**Producer.** `ingestion-service` (M-08) — specifically its webhook receiver,
CDC poller, or backfill enqueuer.

**Consumers.** `ingestion-service` lane workers (to begin work); observability;
audit. (Downstream pipeline stages do **not** consume the lifecycle events; they
consume `content.ingested`.)

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `ingestion_id` | uuid (v7) | yes | The ingestion unit. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7)\|null | no | Target workspace if known at request time. |
| `connector_id` | uuid (v7) | yes | Connector this ingestion came through. |
| `provider` | enum | yes | `zendesk\|jira\|notion\|confluence\|slack\|linear\|gong\|salesforce`. |
| `external_event_id` | string | yes | Source idempotency key (dedupe). |
| `request_kind` | enum | yes | `webhook\|cdc_poll\|backfill`. |
| `lane` | enum | yes | `live\|standard\|bulk`. |
| `requested_at` | timestamp | yes | When the request was admitted. |

**Example JSON**

```json
{
  "event_id": "019ec6ff-0000-7000-8000-000000000f01",
  "event_type": "IngestionRequested",
  "aggregate_type": "ingestion",
  "aggregate_id": "019ec6ff-1111-7000-8000-000000000a01",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:20.004Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": null,
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "ingestion_id": "019ec6ff-1111-7000-8000-000000000a01",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "connector_id": "019ec6fe-2222-7000-8000-000000000b10",
    "provider": "zendesk",
    "external_event_id": "zd-ticket-558231-comment-7",
    "request_kind": "webhook",
    "lane": "live",
    "requested_at": "2026-06-14T09:31:20.004Z"
  }
}
```

> `correlation_id` is minted here and becomes the thread that follows this
> content through screening, document creation, chunking, and indexing.
> `causation_id` is null because a source webhook is an external root cause.

---

## IngestionStarted

**Purpose.** Record that a lane worker has begun processing a requested
ingestion unit (normalization, ACL capture, lane-routed work). Marks the
transition REQUESTED → STARTED for status and latency measurement.

**Producer.** `ingestion-service` (M-08) lane worker.

**Consumers.** Observability (latency/lane SLO tracking); audit.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `ingestion_id` | uuid (v7) | yes | The ingestion unit. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `connector_id` | uuid (v7) | yes | Connector. |
| `lane` | enum | yes | `live\|standard\|bulk`. |
| `attempt` | integer | yes | Attempt number (1 on first try; increments on retry). |
| `worker_id` | string | no | Opaque worker/instance identifier for tracing. |
| `started_at` | timestamp | yes | When work began. |

**Example JSON**

```json
{
  "event_id": "019ec6ff-0000-7000-8000-000000000f02",
  "event_type": "IngestionStarted",
  "aggregate_type": "ingestion",
  "aggregate_id": "019ec6ff-1111-7000-8000-000000000a01",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:20.250Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec6ff-0000-7000-8000-000000000f01",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "ingestion_id": "019ec6ff-1111-7000-8000-000000000a01",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "connector_id": "019ec6fe-2222-7000-8000-000000000b10",
    "lane": "live",
    "attempt": 1,
    "worker_id": "ingest-live-3",
    "started_at": "2026-06-14T09:31:20.250Z"
  }
}
```

---

## IngestionCompleted

**Purpose.** Record that an ingestion unit finished successfully: content was
normalized, the source ACL captured, and the content payload(s) handed to the
pipeline. The accompanying `content.ingested` envelope(s) carry the actual
content into screening (M-09).

**Producer.** `ingestion-service` (M-08) lane worker.

**Consumers.** Observability; audit. (The *content* hand-off to screening is the
separate `content.ingested` event, not this lifecycle event.)

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `ingestion_id` | uuid (v7) | yes | The ingestion unit. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `connector_id` | uuid (v7) | yes | Connector. |
| `lane` | enum | yes | `live\|standard\|bulk`. |
| `envelope_count` | integer | yes | Number of `content.ingested` envelopes emitted. |
| `content_ref_ids` | uuid[] | yes | Ids of the emitted ingress envelopes (`ingestion_events` rows). |
| `read_principals_captured` | boolean | yes | Whether source ACLs were captured for the content. |
| `attempts` | integer | yes | Total attempts taken to succeed. |
| `completed_at` | timestamp | yes | When the unit completed. |

**Example JSON**

```json
{
  "event_id": "019ec6ff-0000-7000-8000-000000000f03",
  "event_type": "IngestionCompleted",
  "aggregate_type": "ingestion",
  "aggregate_id": "019ec6ff-1111-7000-8000-000000000a01",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:20.980Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec6ff-0000-7000-8000-000000000f02",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "ingestion_id": "019ec6ff-1111-7000-8000-000000000a01",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "connector_id": "019ec6fe-2222-7000-8000-000000000b10",
    "lane": "live",
    "envelope_count": 1,
    "content_ref_ids": ["019ec6ff-3333-7000-8000-000000000d01"],
    "read_principals_captured": true,
    "attempts": 1,
    "completed_at": "2026-06-14T09:31:20.980Z"
  }
}
```

---

## IngestionFailed

**Purpose.** Record a terminal failure of an ingestion unit — either a
non-retryable error (bad HMAC, deauthorized connector, malformed payload, tenant
kill-switch) or exhaustion of the bounded retry policy. The unit moves to a
dead-letter state for inspection/replay; raw envelope retained where possible.

**Producer.** `ingestion-service` (M-08).

**Consumers.** Observability / alerting (paged if a connector's failure rate
breaches its SLO); audit; ops dead-letter tooling.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `ingestion_id` | uuid (v7) | yes | The ingestion unit. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `connector_id` | uuid (v7) | yes | Connector. |
| `lane` | enum | yes | `live\|standard\|bulk`. |
| `failure_class` | enum | yes | `retryable_exhausted\|non_retryable`. |
| `failure_reason` | enum | yes | `hmac_invalid\|connector_deauthorized\|malformed_payload\|source_unavailable\|rate_limited\|kill_switch_engaged\|internal_error`. |
| `attempts` | integer | yes | Total attempts before failing. |
| `dead_lettered` | boolean | yes | Whether the unit was parked for replay. |
| `detail` | string\|null | no | Safe, non-sensitive diagnostic detail (never includes credentials or raw PII). |
| `failed_at` | timestamp | yes | When the terminal failure was recorded. |

**Example JSON**

```json
{
  "event_id": "019ec6ff-0000-7000-8000-000000000f04",
  "event_type": "IngestionFailed",
  "aggregate_type": "ingestion",
  "aggregate_id": "019ec6ff-1111-7000-8000-000000000a09",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:33:02.117Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c44",
  "causation_id": "019ec6ff-0000-7000-8000-000000000f41",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "ingestion_id": "019ec6ff-1111-7000-8000-000000000a09",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "connector_id": "019ec6fe-2222-7000-8000-000000000b10",
    "lane": "standard",
    "failure_class": "retryable_exhausted",
    "failure_reason": "source_unavailable",
    "attempts": 5,
    "dead_lettered": true,
    "detail": "source returned 503 on all attempts within backoff window",
    "failed_at": "2026-06-14T09:33:02.117Z"
  }
}
```

---

## Relationship to `content.ingested`

`content.ingested` (from the M-08 spec) is the **content hand-off** to screening
(M-09), emitted alongside `IngestionCompleted`. It is lane-tagged, tenant-stamped,
carries the captured `read_principals`, and references the `ingestion_events`
envelope row. Its full schema is owned by the screening slice (M-09 is its
consumer); this contract file fixes the **lifecycle** events
(`Requested/Started/Completed/Failed`) that are this slice's named deliverable.
The correlation thread (`correlation_id`) minted in `IngestionRequested` is
carried onto `content.ingested` and onward into `DocumentCreated` /
`DocumentProcessingStarted`, giving a single end-to-end trace.
