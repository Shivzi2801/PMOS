# document-service

**Maps to:** M-03 · Core Persistence & Product Hierarchy (documents + hierarchy)
**Wave 0 · Slice 2 — blueprint only.** No endpoints, no logic, no runtime here.

The **system of record** for documents and the product hierarchy. Postgres is
authoritative; everything downstream (chunks, vectors, projections) is derived
from what this service persists. This service is the trunk of the data contract
the whole platform speaks.

---

## 1. Responsibilities

- Own the **document lifecycle**: a `documents` logical handle plus **immutable**
  `document_versions`. Documents have no content column; content lives on a
  version. A new content body is always a *new version*, never an in-place edit.
- Own the **product hierarchy** rows established in Slice 1:
  `products → features → epics → user_stories → requirements`, plus
  `workspaces`. Maintain referential integrity across the tree.
- Maintain the **`documents.current_version_id` pointer** as versions are added.
- Compute and store **`content_hash`** on each version (dedupe key that governs
  whether downstream re-chunking / re-embedding is needed).
- Be the **single writer** of all tables it owns; expose them to the rest of the
  system *only* through events (now) and owner-mediated reads (future).
- Emit the canonical **document lifecycle events** that drive the downstream
  pipeline (chunking, then screening/extraction/indexing in later slices).
- Honor tenant isolation by construction: every row carries `organization_id` +
  `workspace_id`, stamped from tenant context, protected by the Slice-1 RLS
  policy. This service never accepts a tenant id from a payload.

**Explicitly NOT responsible for** (owned elsewhere): receiving external source
content (→ `ingestion-service`), splitting versions into chunks
(→ `chunking-service`), screening/PII/injection (→ M-09), embedding or vector
indexing (→ M-12), retrieval (→ M-13).

---

## 2. Inputs

| Input | Source | Mode |
|---|---|---|
| Document create / new-version commands | BFF (M-05), future slice | Synchronous command (owner-mediated) |
| Hierarchy CRUD (products…requirements) | BFF (M-05), future slice | Synchronous command |
| Ingested, screened content ready to persist as a document | `content.normalized` (M-09) — *future* | Event |
| Approved PRD / Story bodies to persist as immutable versions | `prd.approved` / `story.approved` (M-24/M-25) — *future* | Event |
| Tenant context (org, workspace, user, roles) | Request-scoped, set by BFF before any write | Ambient (RLS) |

> In Slice 2 none of these are implemented. They are listed so the boundary is
> unambiguous: this service's writes are driven by commands routed through the
> BFF and by upstream events — never by another service reaching into its tables.

---

## 3. Outputs

| Output | Form | Consumed by |
|---|---|---|
| Document + version persisted | Postgres rows (owned tables) | self (source of truth) |
| `DocumentCreated` | Event | `chunking-service`, audit, projections |
| `DocumentUpdated` | Event | projections, search-staleness, audit |
| `DocumentDeleted` | Event | `chunking-service` (tombstone), index teardown (future) |
| `DocumentProcessingStarted` | Event | observability, Brief/status surfaces |
| `DocumentProcessingCompleted` | Event | observability, Brief/status surfaces |
| Hierarchy change events (`product.*`…`requirement.*`) | Events | projections, search-staleness, audit |
| Document reads (future) | Synchronous response via BFF | clients, lenses |

Full payload schemas and examples: see `contracts/document-events.md`.

---

## 4. Database ownership

**Sole writer** of (from Slice 1 migrations):

- `documents` — logical handle, `doc_type`, `current_version_id` pointer.
- `document_versions` — immutable snapshots; `content`, `content_hash`,
  `version_number` (unique per document), `storage_ref`.
- `workspaces` — the soft (app-layer) boundary row.
- `products`, `features`, `epics`, `user_stories`, `requirements` — the strict
  hierarchy.

**Does not own / never writes:** `document_chunks` (owned by
`chunking-service`), any ingestion table, any downstream derived store.

All owned tables inherit the Slice-1 conventions: UUIDv7 PKs, `timestamptz`,
`updated_at` trigger, soft delete via `deleted_at`, `organization_id` +
`workspace_id` on every row, `FORCE ROW LEVEL SECURITY` with the canonical
org-isolation policy.

---

## 5. Events published

Canonical names (schemas in `contracts/document-events.md`):

- **`DocumentCreated`** — a new document handle exists (with its first version).
- **`DocumentUpdated`** — a new immutable version was committed (or metadata
  such as title/type changed); carries the new `version_id`.
- **`DocumentDeleted`** — the document was soft-deleted; downstream must
  tombstone derived data (chunks, future vectors), never silently drop it.
- **`DocumentProcessingStarted`** — this document has entered the
  enrichment pipeline (chunking → screening → … ); a status signal for
  observability and the Brief.
- **`DocumentProcessingCompleted`** — the pipeline finished (or terminally
  failed) for this document version; carries the terminal outcome.

Hierarchy events (`product.*`, `feature.*`, `epic.*`, `user_story.*`,
`requirement.*`, `workspace.created`) are published on the same envelope but are
not expanded in this slice's contract files (the five document events are the
slice's named deliverable).

All events are emitted via the **transactional outbox** (M-04): the row write
and the outbox row are one transaction. Each event carries `organization_id`,
`correlation_id`, and `causation_id`.

---

## 6. Events consumed

In Slice 2 the consumed set is documented for boundary clarity; none are wired:

- **`content.normalized`** (M-09, *future*) — screened, normalized content ready
  to become a document version. This is the inbound seam from the ingestion side
  of the pipeline. `document-service` decides persistence; it does not screen.
- **`prd.approved` / `story.approved`** (M-24 / M-25, *future*) — approved AI
  outputs that must be frozen as immutable document versions and committed to
  the hierarchy (`epics` / `user_stories` / `requirements`).

This service does **not** consume ingestion-service events directly; the
ingestion → document seam passes through screening (M-09) in the full pipeline.
The direct event this slice exercises end-to-end is the *outbound* hand-off to
`chunking-service` (`DocumentCreated` / `DocumentUpdated`).

---

## 7. Future APIs (documentation only — not built in this slice)

Reserved surface, all owner-mediated through the BFF (M-05), all REST commands
carrying a mandatory `Idempotency-Key`, all reads cursor-paginated:

| Method & path (future) | Purpose |
|---|---|
| `POST /api/v1/documents` | Create a document + first version (idempotent). |
| `GET /api/v1/documents/{id}` | Read a document and its current version. |
| `GET /api/v1/documents/{id}/versions` | List immutable versions (cursor). |
| `GET /api/v1/documents/{id}/versions/{versionId}` | Read a specific version. |
| `POST /api/v1/documents/{id}/versions` | Commit a new immutable version. |
| `DELETE /api/v1/documents/{id}` | Soft-delete (emits `DocumentDeleted`). |
| `POST/GET/PATCH/DELETE /api/v1/{products\|features\|epics\|user-stories\|requirements}[/{id}]` | Hierarchy CRUD. |
| `POST/GET/PATCH /api/v1/workspaces[/{id}]` | Workspace lifecycle. |

Request/response/error shapes, idempotency semantics, correlation, and tracing
are defined once in `contracts/service-interfaces.md` and apply to every row
above. No endpoint is implemented in Slice 2.
