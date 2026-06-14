# Contract · Processing Events

**Wave 0 · Slice 3 — canonical event definitions.** Wire contracts only.

These seven events describe a document's progress through the processing
pipeline. They share the **canonical envelope** defined in Slice 2
(`services/contracts/service-interfaces.md` §Event envelope): `event_id`,
`event_type`, `aggregate_type`, `aggregate_id`, `organization_id`, `occurred_at`,
`published_at`, `correlation_id`, `causation_id`, `schema_version`, `payload`.
The schemas below define the **`payload`** body.

Conventions (carried from Slice 2):
- All ids are UUIDv7 strings; all timestamps RFC 3339 UTC; `schema_version`
  starts at `1`.
- Every payload repeats `organization_id` (self-contained tenant filtering).
- Delivery is at-least-once via the M-04 outbox; consumers dedupe on `event_id`.
- Ordering is per-aggregate (`aggregate_id` = `document_id` for these events).
- The `correlation_id` minted at ingestion is propagated onto all of these, so
  one document's pipeline journey is a single trace.

These processing events complement the Slice-2 document/ingestion events. They
are emitted by the processing stages and reconciled into the document's
lifecycle state (owned by `document-service`).

---

## DocumentQueued

**Purpose.** A committed document version has been admitted to the processing
pipeline and enqueued on its priority lane. Marks **Uploaded → Queued**.

**Producer.** `document-service` (the lifecycle-state owner) at queue admission.

**Consumer.** Per-lane processing workers (future); observability (queue depth /
lane SLO); the state machine.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document. |
| `document_version_id` | uuid (v7) | yes | The version being processed. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `lane` | enum | yes | `live\|standard\|bulk`. |
| `content_hash` | string | yes | Version body hash (dedupe key). |
| `queued_at` | timestamp | yes | When admitted to the queue. |

**Example JSON**

```json
{
  "event_id": "019ec910-0000-7000-8000-000000000101",
  "event_type": "DocumentQueued",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:22.700Z",
  "published_at": "2026-06-14T09:31:22.740Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec700-0000-7000-8000-0000000000e1",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "lane": "live",
    "content_hash": "sha256:9f2b1c",
    "queued_at": "2026-06-14T09:31:22.700Z"
  }
}
```

---

## DocumentValidated

**Purpose.** Validation + tenant verification + screening have passed; the
version is admissible and clean (not quarantined). Marks the transition into
active **Processing** beyond validation.

**Producer.** Processing worker (validation/screening stage; M-09 in the full
pipeline).

**Consumer.** `chunking-service` (clear to chunk); `document-service` (state);
observability; audit.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document. |
| `document_version_id` | uuid (v7) | yes | The version. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `validation_result` | enum | yes | `passed` (only `passed` is emitted; failures emit `DocumentFailed`). |
| `screening_verdict` | enum | yes | `clean\|quarantined`. (Quarantined does not proceed to chunking.) |
| `pii_findings` | integer | yes | Count of PII findings (redacted/flagged upstream). |
| `lane` | enum | yes | `live\|standard\|bulk`. |
| `validated_at` | timestamp | yes | When validation completed. |

**Example JSON**

```json
{
  "event_id": "019ec911-0000-7000-8000-000000000102",
  "event_type": "DocumentValidated",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:23.100Z",
  "published_at": "2026-06-14T09:31:23.130Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec910-0000-7000-8000-000000000101",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "validation_result": "passed",
    "screening_verdict": "clean",
    "pii_findings": 0,
    "lane": "live",
    "validated_at": "2026-06-14T09:31:23.100Z"
  }
}
```

---

## DocumentChunked

**Purpose.** All chunks for the version have been written to `document_chunks`.
Hands the chunk set to the embedding stage. Marks **Processing → Chunked**.

**Producer.** `chunking-service`.

**Consumer.** Embedding / index fan-out (M-12, future); `document-service`
(state); observability.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document. |
| `document_version_id` | uuid (v7) | yes | The version chunked. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `chunk_count` | integer | yes | Number of chunks written. |
| `chunk_id_range` | object | yes | `{first, last}` UUIDv7 chunk ids (the batch). |
| `chunking_config_version` | string | yes | Strategy+config that produced the batch. |
| `read_principals_present` | boolean | yes | Whether chunks carry source ACLs. |
| `chunked_at` | timestamp | yes | When chunking completed. |

**Example JSON**

```json
{
  "event_id": "019ec912-0000-7000-8000-000000000103",
  "event_type": "DocumentChunked",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:23.900Z",
  "published_at": "2026-06-14T09:31:23.930Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec911-0000-7000-8000-000000000102",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "chunk_count": 42,
    "chunk_id_range": {
      "first": "019ec900-0000-7000-8000-0000000000c1",
      "last": "019ec900-0000-7000-8000-0000000000fa"
    },
    "chunking_config_version": "hybrid-v1",
    "read_principals_present": true,
    "chunked_at": "2026-06-14T09:31:23.900Z"
  }
}
```

---

## DocumentEmbedded

**Purpose.** Vectors have been generated for the version's chunks and the chunk
rows' embedding bookkeeping written back. Marks **Chunked → Embedded**.

**Producer.** Index fan-out / embedding stage (M-12, future).

**Consumer.** Index upsert stage (M-12); `document-service` (state);
observability (embedding throughput / cost).

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document. |
| `document_version_id` | uuid (v7) | yes | The version. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `embedding_model` | string | yes | Model identifier used. |
| `embedding_dimension` | integer | yes | Vector dimension (collection-fixed). |
| `embedded_count` | integer | yes | Chunks embedded this run. |
| `deduped_count` | integer | yes | Chunks skipped via `content_hash` reuse. |
| `collection` | string | yes | Target dimension-fixed collection (blue/green aware). |
| `embedded_at` | timestamp | yes | When embedding completed. |

**Example JSON**

```json
{
  "event_id": "019ec913-0000-7000-8000-000000000104",
  "event_type": "DocumentEmbedded",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:30.400Z",
  "published_at": "2026-06-14T09:31:30.430Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec912-0000-7000-8000-000000000103",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "embedding_model": "text-embedding-3-large",
    "embedding_dimension": 3072,
    "embedded_count": 40,
    "deduped_count": 2,
    "collection": "chunks_te3l_3072_blue",
    "embedded_at": "2026-06-14T09:31:30.400Z"
  }
}
```

---

## DocumentIndexed

**Purpose.** The version's vectors (and sparse representations) plus payloads
(`organization_id`, `read_principals`, metadata) have been upserted into the
index. Marks **Embedded → Indexed**.

**Producer.** Index fan-out (M-12, future).

**Consumer.** Reconciler (parity check → Ready); `document-service` (state);
observability.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document. |
| `document_version_id` | uuid (v7) | yes | The version. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `indexed_count` | integer | yes | Points upserted. |
| `collection` | string | yes | Collection the points live in. |
| `payload_schema_version` | integer | yes | Version of the point payload schema. |
| `indexed_at` | timestamp | yes | When index upsert confirmed. |

**Example JSON**

```json
{
  "event_id": "019ec914-0000-7000-8000-000000000105",
  "event_type": "DocumentIndexed",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:31.250Z",
  "published_at": "2026-06-14T09:31:31.280Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec913-0000-7000-8000-000000000104",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "indexed_count": 42,
    "collection": "chunks_te3l_3072_blue",
    "payload_schema_version": 1,
    "indexed_at": "2026-06-14T09:31:31.250Z"
  }
}
```

---

## DocumentReady

**Purpose.** Postgres↔index parity confirmed; the document is fully retrievable.
Terminal **success** of processing. Marks **Indexed → Ready**. The only state in
which a document contributes to retrieval results.

**Producer.** `document-service` (lifecycle-state owner) after reconciliation.

**Consumer.** Retrieval (M-13) treats Ready documents as queryable; Brief / status
surfaces; agents; observability.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document. |
| `document_version_id` | uuid (v7) | yes | The ready version. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `chunk_count` | integer | yes | Retrievable chunks. |
| `collection` | string | yes | Collection serving this document. |
| `ready_at` | timestamp | yes | When the document became retrievable. |
| `processing_duration_ms` | integer | yes | End-to-end pipeline duration (queued→ready). |

**Example JSON**

```json
{
  "event_id": "019ec915-0000-7000-8000-000000000106",
  "event_type": "DocumentReady",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:31.900Z",
  "published_at": "2026-06-14T09:31:31.930Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec914-0000-7000-8000-000000000105",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "chunk_count": 42,
    "collection": "chunks_te3l_3072_blue",
    "ready_at": "2026-06-14T09:31:31.900Z",
    "processing_duration_ms": 9200
  }
}
```

---

## DocumentFailed

**Purpose.** Processing reached a failure at some stage. Drives the document to
the **Failed** lifecycle state (a recoverable holding state). Carries the stage,
classification, and whether the unit was dead-lettered, so recovery/retry can be
decided. Never silent.

**Producer.** The stage that failed (validation/screening, chunking, embedding,
or indexing); `document-service` reconciles it into the Failed state.

**Consumer.** Observability / alerting; ops dead-letter + retry tooling;
`document-service` (state); audit.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document. |
| `document_version_id` | uuid (v7)\|null | no | The version, if one was committed. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `failed_stage` | enum | yes | `validation\|screening\|chunking\|embedding\|indexing`. |
| `failure_class` | enum | yes | `retryable_exhausted\|non_retryable`. |
| `failure_reason` | enum | yes | `malformed\|tenant_unresolved\|injection_quarantined\|unchunkable\|embedding_provider_error\|dimension_mismatch\|index_unavailable\|internal_error`. |
| `attempts` | integer | yes | Attempts before failing. |
| `dead_lettered` | boolean | yes | Whether parked for replay. |
| `detail` | string\|null | no | Safe, non-sensitive diagnostic (never PII/credentials). |
| `failed_at` | timestamp | yes | When the failure was recorded. |

**Example JSON**

```json
{
  "event_id": "019ec916-0000-7000-8000-000000000107",
  "event_type": "DocumentFailed",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000b7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:34:10.050Z",
  "published_at": "2026-06-14T09:34:10.090Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c44",
  "causation_id": "019ec912-0000-7000-8000-000000000190",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000b7",
    "document_version_id": "00000000-0000-7000-8000-0000000000b8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "failed_stage": "embedding",
    "failure_class": "retryable_exhausted",
    "failure_reason": "embedding_provider_error",
    "attempts": 5,
    "dead_lettered": true,
    "detail": "embedding provider returned 503 across all retry attempts",
    "failed_at": "2026-06-14T09:34:10.050Z"
  }
}
```

---

## Event → state map

| Event | Drives transition |
|---|---|
| `DocumentQueued` | Uploaded → Queued |
| `DocumentValidated` | Queued → Processing (validation cleared) |
| `DocumentChunked` | Processing → Chunked |
| `DocumentEmbedded` | Chunked → Embedded |
| `DocumentIndexed` | Embedded → Indexed |
| `DocumentReady` | Indexed → Ready |
| `DocumentFailed` | any active state → Failed |

Recovery (`Failed → Queued`) is driven by an operator/auto-retry command, not a
processing event; it re-emits `DocumentQueued` for the same version. See
`processing-state-machine.md`.
