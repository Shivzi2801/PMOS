# Contract · Document Events

**Wave 0 · Slice 2 — canonical event definitions.** These are wire contracts,
not code. They are versioned and additive-first: fields may be added within a
major version; a breaking change is a new event type or a new `schema_version`
major. All events are delivered via the M-04 transactional outbox with
at-least-once semantics; consumers must be idempotent (dedupe on `event_id`).

All events share the canonical **envelope** defined in
`service-interfaces.md` (§ Event envelope). The `payload` schemas below are the
contents of the envelope's `payload` field. Every payload carries
`organization_id` (redundant with the envelope, intentionally, so a payload is
self-contained for tenant filtering).

**Common payload conventions**
- All ids are UUIDv7 strings.
- All timestamps are RFC 3339 / ISO 8601 UTC (`...Z`), matching Postgres
  `timestamptz`.
- `schema_version` is an integer major version, starting at `1`.
- Tenant fields (`organization_id`, `workspace_id`) are stamped from context,
  never client-supplied.

---

## DocumentCreated

**Purpose.** Announce that a new document handle exists together with its first
immutable version. This is the trigger that starts the enrichment pipeline for
brand-new content.

**Producer.** `document-service` (M-03).

**Consumers.** `chunking-service` (begins chunking the first version);
audit fabric (M-21, future); projection builders / search-staleness (future);
observability.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | Major version of this payload (=1). |
| `document_id` | uuid (v7) | yes | The new document handle. |
| `organization_id` | uuid (v7) | yes | Tenant; consumers MUST filter on it. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `document_version_id` | uuid (v7) | yes | The first immutable version. |
| `version_number` | integer | yes | Version sequence (=1 for created). |
| `doc_type` | enum | yes | `note\|prd\|story_set\|interview\|insight\|import`. |
| `title` | string | yes | Document title at creation. |
| `content_hash` | string | yes | Hash of the first version body (dedupe key). |
| `storage_ref` | string\|null | no | Blob reference if the body is stored externally. |
| `created_at` | timestamp | yes | When the document was created. |

**Example JSON**

```json
{
  "event_id": "019ec700-0000-7000-8000-0000000000e1",
  "event_type": "DocumentCreated",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:22.512Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec6ff-bbbb-7000-8000-000000000b07",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "version_number": 1,
    "doc_type": "prd",
    "title": "Checkout PRD",
    "content_hash": "sha256:9f2b1c…",
    "storage_ref": null,
    "created_at": "2026-06-14T09:31:22.512Z"
  }
}
```

---

## DocumentUpdated

**Purpose.** Announce that a new immutable version was committed for an existing
document (or that document-level metadata such as `title` / `doc_type` changed).
Because versions are immutable, an "update" to content is always a *new
version* — this event names it. Triggers re-chunking of the new version.

**Producer.** `document-service` (M-03).

**Consumers.** `chunking-service` (re-chunk the new version, supersede the old);
projections / search-staleness; audit; observability.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The document handle. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `document_version_id` | uuid (v7) | yes | The newly committed version. |
| `version_number` | integer | yes | Monotonic version sequence (>1). |
| `previous_version_id` | uuid (v7)\|null | no | The version this supersedes, if any. |
| `change_kind` | enum | yes | `new_version\|metadata_only`. |
| `content_hash` | string\|null | no | Hash of the new version body (null if `metadata_only`). |
| `title` | string | yes | Current title (post-change). |
| `doc_type` | enum | yes | Current doc type. |
| `updated_at` | timestamp | yes | When the change was committed. |

**Example JSON**

```json
{
  "event_id": "019ec701-0000-7000-8000-0000000000e2",
  "event_type": "DocumentUpdated",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T10:05:40.118Z",
  "correlation_id": "019ec700-aaaa-7000-8000-000000000c02",
  "causation_id": "019ec700-bbbb-7000-8000-000000000c50",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "document_version_id": "00000000-0000-7000-8000-0000000000aa",
    "version_number": 2,
    "previous_version_id": "00000000-0000-7000-8000-0000000000a8",
    "change_kind": "new_version",
    "content_hash": "sha256:c41d8a…",
    "title": "Checkout PRD",
    "doc_type": "prd",
    "updated_at": "2026-06-14T10:05:40.118Z"
  }
}
```

---

## DocumentDeleted

**Purpose.** Announce a soft-delete of a document. Downstream derived data
(chunks now; vectors later) must be **tombstoned**, never silently dropped, so
the system stays explainable and the 30-day erasure window is honored.

**Producer.** `document-service` (M-03).

**Consumers.** `chunking-service` (tombstone all chunks for the document);
index teardown (M-12, future); audit; projections.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | The soft-deleted document. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `deletion_kind` | enum | yes | `soft_delete\|purge`. |
| `reason` | enum\|null | no | `user_request\|gdpr_erasure\|retention\|admin`. |
| `deleted_at` | timestamp | yes | When the soft-delete occurred. |
| `purge_after` | timestamp\|null | no | When hard purge becomes eligible (e.g. +30d). |

**Example JSON**

```json
{
  "event_id": "019ec702-0000-7000-8000-0000000000e3",
  "event_type": "DocumentDeleted",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T11:12:03.900Z",
  "correlation_id": "019ec701-aaaa-7000-8000-000000000c03",
  "causation_id": "019ec701-bbbb-7000-8000-000000000c91",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "deletion_kind": "soft_delete",
    "reason": "user_request",
    "deleted_at": "2026-06-14T11:12:03.900Z",
    "purge_after": "2026-07-14T11:12:03.900Z"
  }
}
```

---

## DocumentProcessingStarted

**Purpose.** Signal that a document version has entered the enrichment pipeline
(chunking → screening → extraction → indexing). A status/observability signal;
also surfaced on status and Brief surfaces so users see "processing".

**Producer.** `document-service` (M-03) emits it as the pipeline owner when a
version is dispatched; `chunking-service` is the first stage that acts on it.
(One producer per event type: `document-service` is the canonical producer; the
first processing stage references it via `causation_id`.)

**Consumers.** Observability / status surfaces; Brief (M-32, future);
`chunking-service` (as the first stage); audit.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | Document being processed. |
| `document_version_id` | uuid (v7) | yes | The specific version entering the pipeline. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `pipeline_stage` | enum | yes | First stage: `chunking` (others enumerated for forward-compat: `screening\|extraction\|entity_resolution\|indexing`). |
| `lane` | enum | yes | `live\|standard\|bulk` — preserved from ingestion. |
| `started_at` | timestamp | yes | When processing began. |

**Example JSON**

```json
{
  "event_id": "019ec703-0000-7000-8000-0000000000e4",
  "event_type": "DocumentProcessingStarted",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:23.004Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec700-0000-7000-8000-0000000000e1",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "pipeline_stage": "chunking",
    "lane": "live",
    "started_at": "2026-06-14T09:31:23.004Z"
  }
}
```

> Note the `causation_id` equals the `event_id` of the `DocumentCreated` that
> caused processing, while `correlation_id` matches the original ingestion —
> this is how a single piece of content is traced end-to-end.

---

## DocumentProcessingCompleted

**Purpose.** Signal that pipeline processing for a document version reached a
terminal outcome (success or terminal failure). Closes the processing loop for
status surfaces and lets dependent work (e.g. index readiness, Brief freshness)
proceed or escalate.

**Producer.** `document-service` (M-03) as pipeline owner; the terminating stage
(`chunking-service` for this slice's scope) supplies the outcome via its own
hand-off event, which `document-service` reconciles into this signal.

**Consumers.** Observability / status; Brief (future); `index fan-out` readiness
(future); audit.

**Payload schema**

| Field | Type | Req | Description |
|---|---|---|---|
| `schema_version` | integer | yes | =1. |
| `document_id` | uuid (v7) | yes | Document processed. |
| `document_version_id` | uuid (v7) | yes | Version processed. |
| `organization_id` | uuid (v7) | yes | Tenant. |
| `workspace_id` | uuid (v7) | yes | Owning workspace. |
| `outcome` | enum | yes | `succeeded\|failed`. |
| `last_stage` | enum | yes | Stage at which processing ended (e.g. `chunking`). |
| `chunk_count` | integer\|null | no | Chunks produced (when `succeeded` through chunking). |
| `failure_reason` | string\|null | no | Terminal reason when `outcome=failed`. |
| `lane` | enum | yes | `live\|standard\|bulk`. |
| `completed_at` | timestamp | yes | When the terminal outcome was recorded. |

**Example JSON**

```json
{
  "event_id": "019ec704-0000-7000-8000-0000000000e5",
  "event_type": "DocumentProcessingCompleted",
  "aggregate_type": "document",
  "aggregate_id": "00000000-0000-7000-8000-0000000000a7",
  "organization_id": "00000000-0000-7000-8000-00000000000a",
  "occurred_at": "2026-06-14T09:31:24.871Z",
  "correlation_id": "019ec6ff-aaaa-7000-8000-000000000c01",
  "causation_id": "019ec703-0000-7000-8000-0000000000e4",
  "schema_version": 1,
  "payload": {
    "schema_version": 1,
    "document_id": "00000000-0000-7000-8000-0000000000a7",
    "document_version_id": "00000000-0000-7000-8000-0000000000a8",
    "organization_id": "00000000-0000-7000-8000-00000000000a",
    "workspace_id": "00000000-0000-7000-8000-0000000000a1",
    "outcome": "succeeded",
    "last_stage": "chunking",
    "chunk_count": 42,
    "failure_reason": null,
    "lane": "live",
    "completed_at": "2026-06-14T09:31:24.871Z"
  }
}
```

---

## Related event: `document.chunked` (producer hand-off note)

`document.chunked` (from the M-03 spec) is the concrete hand-off from
`chunking-service` to index fan-out (M-12). It is produced by `chunking-service`
when a version's chunks are written, and is the event M-12's `embedding-worker`
consumes. Its payload references `document_version_id`, the chunk-id batch/range,
and carries `organization_id` + `read_principals` propagation guarantees. It is
listed here for completeness; its full schema is finalized in the indexing slice
(M-12) since M-12 is its primary consumer. In this slice, treat
`DocumentProcessingCompleted` (with `last_stage=chunking`, `chunk_count`) as the
observable completion signal and `document.chunked` as the downstream trigger.
