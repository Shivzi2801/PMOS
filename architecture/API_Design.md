# PMOS — API Design

### The Authoritative API Specification

**Status:** API Constitution v1.0 · **Audience:** frontend engineers, backend engineers, AI engineers, platform/infra engineers, and architects
**Source of truth:** `PMOS_MASTER_SPEC_Final.md` (Constitution v1.0) · `Feature_Inventory.md` (F-01…F-61) · `User_Flows.md`
**Rule of precedence:** Where this document conflicts with a source document, the source documents win on *intent* and this document wins on *interface*. Year-1 implementation is authoritative for what ships now; named end-states (cells, Kafka/MSK, Temporal, generic bitemporal graph) are forward-compatible seams that this API must not foreclose.

---

## 0. How to read this document

This is an interface contract, not a tutorial. It specifies the complete API surface PMOS exposes through the BFF, for every Foundation, MVP, and V1 feature, plus the V2/V1-adjacent endpoints the loop builds toward. Three rules govern the whole document:

1. **The BFF is the only client-facing surface.** No client ever calls a domain service, the Model Gateway, Qdrant, or Postgres directly. Every endpoint below is a BFF endpoint. Internal service-to-service calls are named where they matter for sequencing but are never client-reachable.
2. **`Claim[]` is the wire protocol for AI prose.** Any field that contains AI-generated natural language is typed `Claim[]` (`{text, citations[], kind, confidence}`) at the wire level — never a bare string. This is enforced in the response schemas throughout.
3. **Three protocols, one decision rule.** *Mutates → REST command. Composes ≥3 resources for one screen → GraphQL lens. Server-push → SSE.* The protocol chosen for each endpoint follows this rule and is stated explicitly.

Each feature section below follows a fixed template: **API Purpose · Endpoint · HTTP Method · Authentication Rules · Authorization Rules · Request Schema · Response Schema · Error Responses · Validation Rules · Rate Limits · Idempotency Rules.** Cross-cutting standards (auth, errors, pagination, filtering, versioning) are defined once in §2 and referenced, not repeated, so each feature spec states only its deltas.

---

# Part I — API Strategy & Cross-Cutting Standards

## 1. Protocol Strategy

PMOS speaks three protocols behind one gateway. The selection is not stylistic; it follows the traffic-class model from the spec (interactive reads / interactive writes / autonomous work).

### 1.1 REST Strategy

REST (`/api/v1/...`) carries **commands** (state mutations) and **simple reads** (single-resource fetches, collection lists with cursor pagination). REST is the protocol for everything that changes state and everything a GraphQL lens would be overkill for.

- Base path: `/api/v1`. Structural version is in the URI; behavioral version is the `PMOS-Version` date header (§2.6).
- **Every POST that creates or triggers work carries a mandatory `Idempotency-Key`** (§2.7). POSTs without one are rejected `400 idempotency_key_required`.
- Commands return the mutated resource (`200`/`201`) or, for anything slow, a `202 Accepted` + job resource (§1.4).
- Reads use cursor-only pagination (§2.4); no endpoint ever runs `COUNT(*)` per page.
- Reads never invoke an LLM in the hot path. The single exception is Ask first-token streaming, which is SSE, not REST.

REST is preferred over GraphQL whenever the operation is a mutation, a single-resource read, or a trigger, because commands must be idempotent, auditable, and individually rate-limited — properties GraphQL's single endpoint obscures.

### 1.2 GraphQL Strategy

GraphQL (`/api/graphql`, single endpoint, POST) carries **lenses** — reads that compose three or more resources into one screen: the Brief, the Stream Canvas, the Decision Sheet, the Horizon roadmap, the Arena. A lens is a read-only projection over the graph; it never mutates.

- **Persisted queries only in production.** The client sends a query hash, not query text. Arbitrary query text is rejected in prod (`persisted_query_required`); ad-hoc queries are allowed only in development against the dev fixture.
- **Complexity budget:** cost ≤ 1,000, depth ≤ 8, evaluated *before* execution. Over-budget queries are rejected `422 graphql_complexity_exceeded` with the computed cost in the error meta.
- **DataLoader batching** is mandatory on every resolver that touches a datastore; N+1 patterns are a review blocker.
- **Complexity counts against the rate limit.** A lens with cost 800 consumes 800 units of the caller's GraphQL budget, not "one request."
- Mutations are **not** exposed in GraphQL. If a client needs to change state, it calls a REST command. GraphQL is a read surface by construction; this keeps idempotency, audit, and authorization on the single REST command path.
- All AI prose fields resolve to `Claim[]`; all monetary/quantitative fields resolve from the governed metric store (§ F-15), never from free text.

### 1.3 Streaming / SSE Strategy

SSE carries **server-push**: Ask token/claim streaming, agent-run progress, async-job progress, and the Tide. SSE endpoints live under `/api/v1/...` (e.g. `/api/v1/ask/stream`) and emit `text/event-stream`.

- **Resumable** via `Last-Event-ID`: each event carries a monotonic `id`; on reconnect the client sends `Last-Event-ID` and the server replays from the next event.
- **Heartbeats** every 15s (a `: heartbeat` comment frame) keep intermediaries from closing idle connections.
- **Server-side termination at 15 minutes**; the client resumes with a fresh connection + `Last-Event-ID`. Long agent runs survive arbitrary reconnects this way.
- **Auth:** SSE consumed via `fetch`-based streaming uses the normal `Authorization: Bearer` header. Native `EventSource` (which cannot set headers) uses a **single-use 60-second stream ticket** minted by `POST /api/v1/stream-tickets` — never the session token in a query string. (See §2.1.)
- Event envelope is uniform across all SSE streams (§2.8): `event:` names the type, `data:` is a JSON object, `id:` is the resume cursor.

### 1.4 Async-Job Grammar (one grammar for everything slow)

Every slow operation — exports, imports, backfills, simulations, the Diagnostic, every agent run — uses **one** grammar:

```
POST /api/v1/<resource>            → 202 Accepted + { job: { id, status, progress, links } }
GET  /api/v1/jobs/{jobId}          → current job state (status, progress, result_ref|error)
GET  /api/v1/jobs/{jobId}/stream   → SSE progress (resumable)
POST /api/v1/jobs/{jobId}/cancel   → request cancellation (idempotent)
```

`status ∈ {queued, running, succeeded, failed, cancelled, cancelling}`. `progress` is `{ pct: 0–100, stage: string, detail?: Claim[] }`. On `succeeded`, `result_ref` points at the produced resource (a GraphQL lens id, a file resource, a decision id). On `failed`, the standard error object (§2.2) is in `error`. Agent runs are the canonical instance: `POST /api/v1/runs` returns `202` + a job whose `id` is the `agent_runs.id`.

### 1.5 WebSocket Requirements

PMOS ships **no WebSocket endpoints in Year-1.** The three-protocol contract (REST/GraphQL/SSE) covers every Year-1 interaction; all server-push is unidirectional (token streams, run progress, the Tide), which SSE serves with less operational surface (no sticky bidirectional sessions, native HTTP/2 multiplexing, trivial resume).

WebSockets are reserved for a single named future case: **multiplayer collaborative canvas editing** (concurrent cursor/selection presence on the Meridian canvas, a Year-3 "Operating System" capability if real-time co-editing is validated). Until then, canvas state changes flow through REST commands + outbox-driven SSE invalidation. Designing SSE-first keeps the BFF stateless and horizontally scalable; a WebSocket plane, if ever added, sits beside SSE for presence only and never carries authoritative writes (those stay on the audited REST command path).

---

## 2. Cross-Cutting Standards (defined once, referenced everywhere)

### 2.1 Authentication Standards

- **Identity provider: Clerk.** SSO via SAML/OIDC; MFA enforced for editor+ roles; SCIM for enterprise provisioning. **Passwords never touch PMOS endpoints** — there is no password field on any PMOS API.
- **Bearer tokens.** Every authenticated request carries `Authorization: Bearer <jwt>`. The JWT is Clerk-issued and **JWKS-verified at the BFF** on every request (no opaque session lookups in the hot path). Expired/invalid/unverifiable → `401 unauthenticated`.
- **TenantContext is mandatory.** From the verified JWT the BFF resolves `(organization_id, workspace_id, user_id, roles[], abac_attrs)` and opens a tenant-scoped transaction (`SET LOCAL app.current_org_id`). **Any request that cannot resolve a TenantContext is refused before any data is touched** (`403 tenant_context_unresolved`). This is security checkpoint #1 in every flow.
- **SSE / EventSource:** `POST /api/v1/stream-tickets` mints a single-use, 60-second, audience-scoped ticket bound to `(user, org, stream_kind)`. `EventSource` passes it as `?ticket=`; the ticket is burned on first use. The long-lived session JWT is **never** placed in a URL.
- **Public/unauthenticated surface:** only the marketing-initiated Diagnostic consent handshake (§ F-23) and Clerk's own hosted flows. Everything under `/api/v1` and `/api/graphql` requires a verified JWT, with the Diagnostic running under a scoped, time-boxed trial principal.

### 2.2 Standard Error Format (one shape everywhere)

Every non-2xx response — REST, GraphQL errors, and SSE `error` events — uses one envelope:

```json
{
  "error": {
    "code": "idempotency_conflict",
    "message": "Human-readable, safe-to-display summary.",
    "status": 409,
    "request_id": "req_01J9...",
    "trace_id": "otel-abc123",
    "meta": { "field": "body_hash", "expected": "...", "received": "..." },
    "retryable": false
  }
}
```

`code` is a stable machine string (snake_case); `message` never leaks internal detail or another tenant's data; `request_id` and `trace_id` link to the OTel trace. `retryable` tells clients whether a blind retry is safe. GraphQL surfaces this object inside the standard `errors[].extensions`.

**Canonical codes:** `unauthenticated` (401) · `forbidden` (403) · `tenant_context_unresolved` (403) · `not_found` (404) · `validation_failed` (422) · `idempotency_key_required` (400) · `idempotency_conflict` (409) · `graphql_complexity_exceeded` (422) · `persisted_query_required` (400) · `rate_limited` (429) · `capability_denied` (403) · `approval_required` (409) · `evidence_ungroundable` (422) · `metric_unavailable` (422) · `provider_degraded` (503, with quality badge) · `conflict` (409) · `payload_too_large` (413) · `internal` (500).

### 2.3 Authorization Standards (two principals)

PMOS authorizes **two distinct principals** at two distinct decision points:

- **Human authority = a session claim.** Computed per request as **RBAC × ABAC × source-ACL trim**. RBAC roles: `viewer` (free, unlimited, read-only), `editor` (PM, can draft/commit), `admin` (Olivia: connectors, members, procedural memory, compliance), `owner` (org owner: billing, residency, kill switches). ABAC narrows by Stream membership, residency region, and object sensitivity. **Source-ACL trim** is applied to every read: a result the user could not see in the source system (its `read_principals` exclude them) is removed *pre-fusion* and the omission is surfaced honestly ("n sources withheld by permissions"), never silently dropped.
- **Agent authority = a consumable capability token.** An agent has *no* ambient authority. To call a write/sensitive tool it must hold a capability token bound to `(run_id, task_class, approval_event, ttl=5min)`, issued by the policy engine and verified cryptographically at the governed tool service. An L1 agent cannot call a write tool because no token exists in its run. Year-1 caps issuance at L0–L2 (L2 requires a human approval event); L3/L4 TTL + two-person-rule land with Trust Ladder GA.

Each endpoint below states its **required role**, any **ABAC narrowing**, and — for agent-invoked tools — the **capability token** required.

### 2.4 Pagination Standards (cursor-only)

All collection reads are **cursor-paginated; `COUNT(*)` per page is forbidden.**

- Request: `?limit=<1..100, default 25>&cursor=<opaque>`.
- Response: `{ "data": [...], "page": { "next_cursor": "...|null", "has_more": true } }`.
- The cursor is an opaque, signed, tenant-scoped token encoding the sort key + last-seen UUIDv7 (which is time-ordered, so it doubles as a stable keyset cursor). Cursors are not portable across tenants or filter sets; a mismatched cursor → `422 validation_failed`.
- GraphQL connections follow the same model (`edges`, `pageInfo.endCursor`, `pageInfo.hasNextPage`), backed by keyset pagination, never offset.

### 2.5 Filtering & Sorting Standards

- REST filters are explicit query params with a fixed allow-list per endpoint: `?status=…&created_after=…&stream_id=…`. Unknown params → `422 validation_failed` (fail closed; no silent ignore). Free-form filter DSLs are not accepted on REST.
- Operators where ranges apply: `_after`/`_before` (timestamps), `_in` (comma-separated enum sets), `_gte`/`_lte` (numeric). Example: `?confidence_gte=0.7&kind_in=fact,inference`.
- Sorting: `?sort=<field>&order=<asc|desc>`, default `created_at desc`; only allow-listed fields are sortable (those backed by an index), enforced to protect keyset pagination.
- **Tenant and workspace filters are never accepted from the client.** `organization_id` is force-injected from TenantContext; passing it in a param is ignored at best and `422` if it conflicts. This is the load-bearing isolation rule (mirrored in the Qdrant payload filter).
- Rich/relational filtering for lenses is expressed in the GraphQL query shape (within the complexity budget), not as REST query strings.

### 2.6 API Versioning (two-key, Stripe-style)

- **Structural version in the URI:** `/api/v1`. Reserved for breaking structural changes; PMOS intends never to ship `/v2` (no version proliferation).
- **Behavioral version in a header:** `PMOS-Version: 2026-06-01` (a date). Rolling backward-compatible behavioral improvements are gated behind dated versions; a client pinned to an older date keeps the old behavior. Omitting the header pins the account's default (last version active at onboarding); the resolved version is echoed in the `PMOS-Version` response header.
- GraphQL schema evolution is additive-only within `/v1`: fields are added, never removed or retyped; deprecations carry `@deprecated(reason:)` and a removal date communicated via `PMOS-Version`.

### 2.7 Idempotency Standards

- **Every POST carries `Idempotency-Key`** (a client-generated UUIDv4). Scope of the key is `(organization_id, user_id, method, path, key)`. Missing on a POST → `400 idempotency_key_required`.
- The first request with a given key executes and the full response is cached **24h**. A replay with the same key + same body returns the cached response (same status, same body) without re-executing.
- A replay with the same key but a **different body hash** → `409 idempotency_conflict` (a client bug guard; the meta carries `expected`/`received` body hashes).
- GET/HEAD are inherently idempotent and take no key. PUT/PATCH are idempotent by definition of full/partial replacement but still accept a key for response caching on slow paths. DELETE is soft-delete (idempotent: deleting an already-deleted resource returns the same `200`).
- Ceremonial/financial writes (commit, approval, billing) additionally bind the key into the hash-chain so a replayed commit can never double-append.

### 2.8 SSE Event Envelope

```
id: 000000000042
event: claim
data: {"index":3,"claim":{"text":"...","citations":[...],"kind":"fact","confidence":0.91}}

: heartbeat
```

Event types are namespaced per stream: Ask emits `token | claim | citation | abstention | done | error`; run progress emits `step_started | step_completed | progress | run_completed | run_failed`; the Tide emits `tide_item | tide_clear | interrupt`. Every event has an `id` for `Last-Event-ID` resume.

### 2.9 The `Claim[]` Wire Type

```ts
type Claim = {
  text: string;                       // one sentence/assertion
  citations: Citation[];              // [] only if kind === "inference"
  kind: "fact" | "inference" | "simulated";   // simulated ⇒ rendered violet
  confidence: number;                 // 0..1, calibrated
};
type Citation = {
  source_id: string;                  // resolvable provenance handle
  chunk_id?: string;                  // document_chunks.id (= Qdrant point id)
  uri?: string;                       // deep link into source/object
  evidence_weight: "single" | "corroborated" | "inference" | "simulated" | "degraded";
};
```

Provenance for any `citation.source_id` resolves via `GET /api/v1/provenance/{id}` in **<400ms** (CI-enforced). `kind:"simulated"` is the wire signal for the Violet rule — anything not yet real is violet and must never be presented as fact. Quantitative values inside `text` must trace to a governed metric-store call (§ F-15); a number with no metric citation is a release-blocking defect.

### 2.10 Rate-Limiting Standards

- Limits are enforced at the BFF per `(organization_id, user_id, class)`. Classes: **read** (REST simple reads), **graphql** (counted by computed query cost), **command** (REST POST/PATCH/DELETE), **ask** (LLM-backed streaming), **run** (agent-run creation), **ingest/webhook** (connector inbound).
- Responses carry `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`. Exhaustion → `429 rate_limited` with `Retry-After`.
- Default per-user ceilings (tunable per plan): read 600/min · graphql 20,000 cost-units/min · command 120/min · ask 30/min · run 60/min. Org-level ceilings sit above to bound a tenant's blast radius. Viewers (read-only) get the read/ask/graphql classes only.
- Autonomous/batch work (Overnight PM, backfills) runs on **batch lanes of the Model Gateway** and does not consume interactive ask/run budgets; it is throttled by lane capacity, not per-user limits.

---

# Part II — API Specifications by Domain

This part specifies every endpoint, organized by domain. Each domain maps to the features that produce it (F-IDs in headers). The template per feature is fixed; cross-cutting standards from Part I are referenced by section number rather than repeated. Unless stated otherwise: auth is a JWKS-verified Clerk Bearer JWT with a resolved TenantContext (§2.1); errors use the standard envelope (§2.2); lists are cursor-paginated (§2.4); POSTs require `Idempotency-Key` (§2.7).

---

## 3. Authentication & Session (F-02)

**API Purpose:** Establish and inspect the authenticated session; expose the resolved authority (roles, ABAC, residency) the client needs to render correctly. PMOS holds no credentials — Clerk owns the SSO/MFA/SCIM flows; these endpoints only consume the resulting JWT.

| | |
|---|---|
| **Endpoints** | `GET /api/v1/session` · `POST /api/v1/stream-tickets` · `POST /api/v1/session/logout` |
| **Methods** | GET (session), POST (stream-ticket, logout) |

**Authentication Rules:** `GET /api/v1/session` requires a valid Clerk JWT (JWKS-verified). SSO/SAML/OIDC, MFA, and SCIM provisioning happen entirely in Clerk-hosted flows; PMOS never sees a password. SCIM-deprovisioned users are blocked at JWT verification (Clerk revokes), independent of token expiry.

**Authorization Rules:** Any authenticated principal may read its own session. `stream-tickets` may be minted only for stream kinds the caller is authorized to consume (e.g. a viewer cannot mint a `run_progress` ticket for a run they don't own).

**Request Schema:**
- `GET /api/v1/session` — none.
- `POST /api/v1/stream-tickets` — `{ "stream_kind": "ask" | "run_progress" | "tide" | "job_progress", "resource_id"?: "uuid" }`.

**Response Schema:**
- `GET /api/v1/session` → `200`:
```json
{
  "user": { "id": "uuid", "display_name": "string", "email": "string", "auth_provider": "okta-saml" },
  "organization": { "id": "uuid", "name": "string", "residency_region": "us-east" },
  "workspace": { "id": "uuid", "name": "string" },
  "authority": {
    "roles": ["editor"],
    "abac": { "stream_ids": ["uuid"], "max_sensitivity": "internal" },
    "mfa_satisfied": true
  },
  "pmos_version": "2026-06-01"
}
```
- `POST /api/v1/stream-tickets` → `201 { "ticket": "st_...", "expires_at": "ts", "single_use": true }`.

**Error Responses:** `401 unauthenticated` (bad/expired/unverifiable JWT) · `403 tenant_context_unresolved` (JWT valid but no org binding) · `403 forbidden` (stream kind not permitted for role) · `429 rate_limited`.

**Validation Rules:** `stream_kind` must be in the enum; `run_progress`/`job_progress` require a `resource_id` the caller owns or can view; ticket TTL is fixed server-side at 60s and is not client-settable.

**Rate Limits:** read class for `session`; `stream-tickets` limited to 60/min/user (one per stream open).

**Idempotency Rules:** `stream-tickets` and `logout` are POSTs and take an `Idempotency-Key`, but tickets are single-use by design; a replayed key returns the *same* (already-burned) ticket record so a double-submit cannot mint two live tickets.

---

## 4. Organizations (F-01, F-02, F-41)

**API Purpose:** Manage the hard tenancy boundary — the Organization is the RLS boundary and the unit of residency, billing, identity binding, and kill-switch scope. Org creation is a privileged provisioning action (typically post-purchase), not self-serve.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/organizations` · `GET /api/v1/organizations/{orgId}` · `PATCH /api/v1/organizations/{orgId}` · `GET /api/v1/organizations/{orgId}/identity-binding` · `PUT /api/v1/organizations/{orgId}/identity-binding` |
| **Methods** | POST, GET, PATCH, PUT |

**Authentication Rules:** Standard. Org creation runs under a provisioning principal (platform/owner). Identity-binding writes require `owner` + a re-auth/MFA step (a step-up claim in the JWT).

**Authorization Rules:** Create → platform-provisioning or `owner`. Read → `admin`/`owner` of that org only (RLS guarantees no cross-org read). `PATCH` (name, default policies) → `admin`/`owner`. Identity-binding (`PUT`) → `owner` with step-up MFA. Residency region is set at creation and is **immutable thereafter** via the public API (changing it is a cell-migration operation, Year-2).

**Request Schema:**
- `POST` — `{ "name": "string", "residency_region": "us-east"|"eu-west"|..., "owner_email": "string", "plan": "platform_fee"|"consumption"|"hybrid" }`.
- `PATCH` — `{ "name"?: "string", "default_mfa_policy"?: "editor_plus"|"all", "default_pmos_version"?: "2026-06-01" }`.
- `PUT identity-binding` — `{ "idp": "saml"|"oidc", "metadata_url": "string", "scim_enabled": true }` (binds the org to its IdP via Clerk).

**Response Schema:** Org resource: `{ "id", "name", "residency_region", "plan", "created_at", "identity_binding": { "idp", "scim_enabled" }, "kill_switch_state": {...} }`.

**Error Responses:** `403 forbidden` (insufficient role / missing step-up) · `409 idempotency_conflict` · `409 conflict` (residency region not available in target infra) · `422 validation_failed`.

**Validation Rules:** `residency_region` must be a provisioned region; `name` 1–120 chars; `owner_email` must be a valid, IdP-resolvable address; residency is write-once.

**Rate Limits:** command class; org-create additionally gated to provisioning principals (very low ceiling).

**Idempotency Rules:** `POST` mandatory key; a replayed create returns the existing org (no duplicate provisioning). `PUT identity-binding` is idempotent by replacement; a key guards double-submit.

---

## 5. Workspaces (F-01, F-03 · Flow 1)

**API Purpose:** A Workspace = one company = one graph. Creating a workspace stamps `organization_id` + `workspace_id` onto row zero, establishes the RLS context, seeds roles, initializes empty memory scopes, and emits `workspace.created`. This is **Flow 1 — Workspace Creation**.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/workspaces` · `GET /api/v1/workspaces/{wsId}` · `PATCH /api/v1/workspaces/{wsId}` · `GET /api/v1/workspaces` (list within org) |
| **Methods** | POST, GET, PATCH |

**Authentication Rules:** Standard; create/patch require `admin`+.

**Authorization Rules:** Create → `admin`/`owner`. Read → any member whose ABAC permits (workspace is the *soft* boundary; org is the *hard* RLS boundary). Patch (name, default Stream, residency confirmation) → `admin`/`owner`.

**Request Schema:**
- `POST` — `{ "name": "string", "region": "us-east", "initial_admin_emails": ["string"], "seed_stream_name"?: "string" }`.
- `PATCH` — `{ "name"?: "string", "default_stream_id"?: "uuid" }`.

**Response Schema:** `201`:
```json
{
  "id": "uuid", "organization_id": "uuid", "name": "string",
  "region": "us-east", "created_at": "ts",
  "brief_state": "empty_but_speaking",
  "next_action": { "code": "connect_first_source", "label": "Connect your first source to begin." }
}
```
The response intentionally lands the admin in an empty-but-speaking Brief (Design Law: the system speaks first).

**Error Responses:** `409 idempotency_conflict` (double-submit) · `409 conflict` (region/residency unavailable; identity-bind failure rolls back the whole creation because outbox + INSERT share one transaction) · `403 forbidden` · `422 validation_failed`.

**Validation Rules:** `name` 1–120 chars, unique within org; `region` must match or be permitted under the org residency; `initial_admin_emails` must resolve via the bound IdP.

**Rate Limits:** command class; workspace-create low ceiling.

**Idempotency Rules:** Mandatory key. The create is a single Postgres transaction (organizations/workspaces INSERT + role seed + outbox row); a Clerk org-bind failure rolls the transaction back so no orphan workspace and no emitted event exist. Replay returns the created workspace.

---

## 6. Teams & Streams (F-36 · membership and the human-curated container)

**API Purpose:** PMOS has no "teams" table in the classic sense — the human-curated container is the **Stream** (a durable area of responsibility), and team-like grouping is membership + ABAC over Streams and the workspace. This domain manages Streams, Lenses, and membership/roles.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/streams` · `GET /api/v1/streams/{id}` · `PATCH /api/v1/streams/{id}` · `DELETE /api/v1/streams/{id}` · `POST /api/v1/streams/{id}/members` · `DELETE /api/v1/streams/{id}/members/{userId}` · `POST /api/v1/lenses` · `GET /api/v1/lenses/{id}` · `POST /api/v1/lenses/{id}/share` · `GET /api/v1/workspaces/{wsId}/members` · `PATCH /api/v1/workspaces/{wsId}/members/{userId}` (role change) |
| **Methods** | POST, GET, PATCH, DELETE |

**Authentication Rules:** Standard.

**Authorization Rules:** Stream create/curate → `editor`+ (a PM owns their Streams). Member add/remove on a Stream → Stream owner or `admin`. Workspace member role changes → `admin`/`owner` only. Lens share → the Lens owner; **share never widens ACL** — recipients see only what their own source-ACL trim permits, so a shared Lens renders trimmed per viewer. Deleting a Stream is soft-delete.

**Request Schema:**
- `POST /streams` — `{ "name": "string", "description"?: "string", "object_refs"?: [{ "context_type", "context_id" }] }`.
- `POST /streams/{id}/members` — `{ "user_id": "uuid", "stream_role": "owner"|"contributor"|"viewer" }`.
- `POST /lenses` — `{ "name": "string", "canvas_config": { ... }, "stream_id"?: "uuid" }` (canvas_config is the saved view).
- `POST /lenses/{id}/share` — `{ "audience": "stream"|"workspace"|"users", "user_ids"?: ["uuid"] }`.
- `PATCH workspaces/{wsId}/members/{userId}` — `{ "role": "viewer"|"editor"|"admin"|"owner" }`.

**Response Schema:** Stream/Lens resources with `id`, `name`, owner, membership, `created_at`, `updated_at`; Lens carries `canvas_config` and `shared_with`.

**Error Responses:** `403 forbidden` (role/ownership) · `404 not_found` (soft-deleted referenced) · `409 conflict` (duplicate stream name in workspace) · `422 validation_failed`.

**Validation Rules:** `object_refs` use the polymorphic `(context_type, context_id)` form and are validated against existing rows; a Lens referencing deleted objects degrades gracefully on render (not a write error). Role changes are validated against the role lattice (no self-demotion of the last `owner`).

**Rate Limits:** command class.

**Idempotency Rules:** All POSTs keyed. Member add is idempotent (re-adding an existing member returns the membership). Role change via PATCH is idempotent by replacement.

---

## 7. Users (F-02, F-06)

**API Purpose:** Read and lightly manage user records. Users are projections of Clerk identities (SCIM-synced); PMOS stores an identity-source label (`auth_provider`) and PMOS-side authority data (roles, ABAC, preferences) — **never credentials**.

| | |
|---|---|
| **Endpoints** | `GET /api/v1/users/me` · `PATCH /api/v1/users/me` (preferences) · `GET /api/v1/users/{id}` · `GET /api/v1/workspaces/{wsId}/users` (list) |
| **Methods** | GET, PATCH |

**Authentication Rules:** Standard. User create/deactivate is **not** a PMOS endpoint — it happens via SCIM in the IdP and syncs through Clerk; PMOS reflects the result.

**Authorization Rules:** `me` endpoints → the authenticated user. Reading another user → any member (display fields only; email/role visible to `admin`+). The user list is `admin`+ for full detail, `editor` for display names (to populate assignment pickers).

**Request Schema:** `PATCH /users/me` — `{ "preferences"?: { "atmosphere"?: "daylight"|"midnight", "locale"?: "en-US", "reduced_motion"?: true, "default_altitude"?: "org"|"stream"|"object" } }`.

**Response Schema:** `{ "id", "display_name", "email"?, "auth_provider", "roles", "preferences", "created_at" }` (email/roles trimmed by viewer authority).

**Error Responses:** `403 forbidden` · `404 not_found` · `422 validation_failed`.

**Validation Rules:** `preferences` keys are allow-listed and typed; unknown keys → `422`. `auth_provider`, `roles`, and identity fields are **read-only** on `PATCH /users/me` (roles change only through workspace member admin or SCIM).

**Rate Limits:** read class; `PATCH /users/me` command class.

**Idempotency Rules:** `PATCH` is idempotent by partial replacement; no key required for preference updates (but accepted).

---

## 8. Connectors & Document Ingestion (F-08, F-09, F-10, F-11, F-12 · Flow 2)

**API Purpose:** Authorize source systems and drive the ingestion pipeline (normalize → screen → extract → entity-resolve → index). The client-facing surface is small (authorize a connector, watch health, review quarantine); the heavy pipeline is autonomous, event-driven, and never client-blocking. This is **Flow 2 — Document Ingestion**.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/connectors` · `GET /api/v1/connectors` · `GET /api/v1/connectors/{id}` · `GET /api/v1/connectors/{id}/health` · `POST /api/v1/connectors/{id}/backfill` · `DELETE /api/v1/connectors/{id}` · `GET /api/v1/connectors/{id}/coverage` · `POST /api/v1/connectors/oauth/callback` · `POST /webhooks/connectors/{provider}` (inbound) · `GET /api/v1/ingestion/quarantine` · `POST /api/v1/ingestion/quarantine/{itemId}/release` |
| **Methods** | POST, GET, DELETE |

**Authentication Rules:** Connector management requires a Clerk JWT + `admin`. The OAuth callback is a redirect-back validated by `state` + PKCE. **Inbound webhooks** (`/webhooks/connectors/{provider}`) are *not* Clerk-authenticated — they are verified by the provider's HMAC signature against a per-connector secret held in the secret store, plus a tenant-routing token; an unverified webhook is dropped (`401`) and never reaches the pipeline.

**Authorization Rules:** Add/remove/backfill → `admin`. Health/coverage read → `admin`/`owner`. Quarantine review/release → `admin`. **Source credentials are never returned by any read endpoint** — only a reference to the secret store and non-sensitive metadata (scopes, status).

**Request Schema:**
- `POST /connectors` — `{ "provider": "zendesk"|"jira"|"notion"|"confluence"|"slack"|"linear"|"gong"|"salesforce", "auth_method": "oauth", "lane_policy"?: "live"|"standard"|"bulk", "scopes": ["read:tickets", ...] }`. Returns an OAuth authorize URL.
- `POST /connectors/{id}/backfill` — `{ "since"?: "ts", "lane": "bulk" }` (async-job grammar → `202`).
- Webhook bodies are provider-shaped; PMOS normalizes them. They are treated as **untrusted data, never instructions** (injection screening applies).

**Response Schema:**
- Connector: `{ "id", "provider", "status": "pending_auth"|"healthy"|"degraded"|"expired", "scopes", "lane_policy", "last_sync_at", "acl_reconciled_at" }`.
- `GET /coverage` → `{ "customer_voice_coverage": 0.41, "upgrade_hint": { "add": "gong", "would_raise_to": 0.78 } }` (honest upgrade framing; never a hard requirement).
- `GET /health` → freshness per lane, webhook gap status, backfill progress.
- `GET /ingestion/quarantine` → list of inert quarantined items `{ id, reason: "injection_suspect"|"pii", source_ref, quarantined_at }` (content rendered inert; never executed).

**Error Responses:** `403 forbidden` · `409 conflict` (provider already connected) · `422 validation_failed` (bad scopes) · `401` on unverified webhook · OAuth: scope-insufficient / token-expiry surfaced as connector `status` + a re-auth action, not a hard API error.

**Validation Rules:** `provider` in enum; requested `scopes` must be read scopes (PMOS reads, never writes back through a connector except via the governed tool service under a capability token — see § F-31). Webhook payloads must pass HMAC verification and size limits; oversize → `413`. All ingested content carries the source's `read_principals`, captured at upsert and **load-bearing** for every later retrieval trim.

**Rate Limits:** command class for management; the `ingest/webhook` class governs inbound volume with per-connector backoff. Backfill runs on the bulk lane and never consumes the live lane's freshness budget (live ≤2 min guaranteed).

**Idempotency Rules:** `POST /connectors` keyed (one connector per provider per workspace; replay returns existing). Webhooks are **idempotent by source event id** — the pipeline dedupes on `(provider, external_event_id)` so retried/duplicate deliveries upsert once. Backfill jobs are keyed; the same key returns the running job.

**Pipeline stages (internal, event-chained; not separately client-callable):**
- **F-09 Screening:** normalize → PII screen → injection screen (`quarantine:injection_suspect`, inert). Layer 1 of injection defense (threat #2).
- **F-10 Extraction:** cheap-first cascade (~70% never touch an LLM) → typed `FeedbackAtom` / `Commitment` / `DecisionCandidate` / `RiskSignal`, each with confidence + source pointer; metered to `agent_runs`.
- **F-11 Entity Resolution & Upsert:** resolve atoms to canonical entities (e.g. → `Account`), upsert into the product hierarchy **with provenance + `read_principals`**.
- **F-12 Index Fan-Out:** embed via Model Gateway (`text-embedding-3-large` @ 3072d) → Qdrant; chunk Postgres `id` *is* the Qdrant point id; `content_hash` prevents re-embedding; nightly reconciler heals drift; `organization_id` force-injected into the Qdrant filter from context (never request params).

**Security checkpoints (Flow 2):** (1) credential custody in secret store; (2) PII screen; (3) injection screen → inert quarantine; (4) source-ACL captured at upsert; (5) tenant filter force-injected into Qdrant.

---

## 9. Documents, Product Hierarchy & Knowledge Repository (F-03, F-24, F-25)

**API Purpose:** CRUD and read for the first-class product hierarchy and the document/knowledge repository — products, features, epics, user stories, requirements, and documents (with immutable versions + chunks). These are the typed objects PMs and agents reason over; there is no generic "work item" table and no folder tree (organization is by retrieval, not hierarchy of folders).

| | |
|---|---|
| **Endpoints** | `POST/GET/PATCH/DELETE /api/v1/products/{id?}` · `.../features/{id?}` · `.../epics/{id?}` · `.../user-stories/{id?}` · `.../requirements/{id?}` · `POST/GET /api/v1/documents/{id?}` · `GET /api/v1/documents/{id}/versions` · `GET /api/v1/documents/{id}/versions/{versionId}` · `POST /api/v1/prds` (commit an approved PRD) · GraphQL hierarchy lenses |
| **Methods** | POST, GET, PATCH, DELETE |

**Authentication Rules:** Standard.

**Authorization Rules:** Read → any member, **source-ACL-trimmed** (chunks the user can't see are excluded; the fact of omission is surfaced). Create/edit → `editor`+. Agent-authored writes carry `ai_generated=true` + `source_run_id` and are produced only through an approved run (§ Agents). Delete is soft-delete (30-day trash → purge = the GDPR path); `UPDATE`/`DELETE` on auditable rows is role-revoked where the audit fabric applies.

**Request Schema (representative — `user-stories`):**
- `POST` — `{ "epic_id": "uuid", "title": "string", "body": "string|Claim[]", "acceptance_criteria": ["Claim[]"], "stream_id"?: "uuid" }`. Human-authored `body` may be a string; agent-authored prose is always `Claim[]`.
- `PATCH` — partial fields; edits to agent-authored artifacts are recorded as **edit-distance** against the generated version (feeds F-20).

**Response Schema:** Typed resource with hierarchy refs, `claims` for any AI prose, `version` info for documents, `freshness` badge, `ai_generated`/`source_run_id` when applicable. Documents return immutable version pointers; the live document is the latest version. Example PRD document:
```json
{
  "id": "uuid", "type": "prd", "title": "string",
  "sections": [{ "heading": "string", "content": "Claim[]" }],
  "version": { "id": "uuid", "n": 3, "immutable": true },
  "ai_generated": true, "source_run_id": "uuid",
  "freshness": { "as_of": "ts", "stale": false }
}
```

**Error Responses:** `403 forbidden` · `404 not_found` (incl. soft-deleted) · `409 conflict` (optimistic-concurrency on `updated_at`/version) · `422 validation_failed`.

**Validation Rules:** FK integrity across the hierarchy (a story must reference a live epic); `acceptance_criteria` non-empty for stories submitted for engineering; immutable versions cannot be edited (a change creates a new version); polymorphic edges only via `(context_type, context_id)` for comments/tags/links.

**Rate Limits:** read/command classes.

**Idempotency Rules:** All POSTs keyed. Document version creation is idempotent on `(document_id, content_hash)` — re-submitting identical content does not mint a new version. `POST /prds` (commit an approved, agent-drafted PRD) is keyed and writes a new immutable version + flags provenance.

---

## 10. Search (F-13, F-33 · Flow 3 — Line "Go")

**API Purpose:** Sub-50ms navigation/retrieval. "Go" mode of the Line: lexical+vector hybrid, ACL-trimmed, **no LLM in the hot path**. The only path to Qdrant is the retrieval service; clients reach it only through this BFF endpoint.

| | |
|---|---|
| **Endpoint** | `GET /api/v1/search` |
| **Method** | GET |

**Authentication Rules:** Standard Clerk JWT (simple read; no stream ticket needed).

**Authorization Rules:** Any member. **Source-ACL pre-fusion trim** is mandatory: candidates the user cannot see in the source are removed before ranking and never appear; if trimming removes results, the response notes withheld counts.

**Request Schema:** query params — `?q=<string>&kind_in=product,feature,epic,story,document,decision&limit=20&stream_id=<uuid?>`. `organization_id` is **never** a param (force-injected).

**Response Schema:** `200`:
```json
{
  "data": [
    { "object_type": "epic", "id": "uuid", "title": "string",
      "altitude_hint": "object", "score": 0.93, "uri": "pmos://..." }
  ],
  "withheld": { "count": 2, "reason": "permissions" },
  "page": { "next_cursor": "...|null", "has_more": false },
  "latency_ms": 31
}
```

**Error Responses:** `422 validation_failed` (unknown filter param — fail closed) · `404`-style honest empty state in-body (`data:[]` + `hint: "retrieval failed — try Ask"`) rather than an error · `429 rate_limited`.

**Validation Rules:** `q` 1–512 chars; `kind_in` allow-listed; exact-match/ID recall (e.g. `PROJ-4112`) served by Qdrant native sparse leg; a per-large-tenant BM25 golden set is monitored — if recall drops below ~0.95 the OpenSearch seam (TD-8) activates without an API change.

**Rate Limits:** read class (600/min default). Budget: **Go <50ms** (CI-enforced); int8 quantization + tiered pre-filter hold latency at 10⁸-chunk scale.

**Idempotency Rules:** N/A (GET).

---

## 11. AI Chat / Ask PMOS (F-13, F-14, F-29, F-33 · Flow 4 — Line "Ask")

**API Purpose:** Grounded, cited, permission-correct question answering — the Org-Wide Product Brain. Streams `Claim[]` over SSE with honest abstention. This is **Flow 4 — Ask PMOS**. Numbers come only from governed metric-store tool calls.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/ask` (initiate) · `GET /api/v1/ask/{conversationId}/stream` (SSE) · `GET /api/v1/conversations/{id}` · `GET /api/v1/provenance/{id}` |
| **Methods** | POST (initiate), GET (SSE stream, history, provenance) |

**Authentication Rules:** Initiate with Clerk JWT. The SSE stream uses `fetch`-streaming with the `Authorization` header, or — for native `EventSource` — a single-use 60s **stream ticket** (§2.1); the session token is never in a URL.

**Authorization Rules:** Any member, **including free viewers** (Ask is the ubiquity engine). Retrieval applies **pre-fusion source-ACL trim**; an answer computed without evidence the asker can see renders "n sources withheld by permissions" — honest, never leaking, never silently omitting the fact of omission.

**Request Schema:** `POST /api/v1/ask` — `{ "question": "string", "conversation_id"?: "uuid", "stream_id"?: "uuid", "mode"?: "ask" }` → `200 { "conversation_id": "uuid", "stream_path": "/api/v1/ask/{id}/stream" }`.

**Response Schema (SSE events):**
```
event: token       data: { "delta": "..." }
event: claim        data: { "index": 0, "claim": { "text","citations","kind","confidence" } }
event: citation     data: { "source_id","chunk_id","uri","evidence_weight" }
event: abstention   data: { "reason": "withheld_by_permissions"|"ungroundable", "withheld_count": 2 }
event: done         data: { "claim_count": 5, "grounded": true }
event: error        data: { "error": { ...standard envelope... } }
```
Each `claim` renders with a Provenance Underline; clicking resolves `GET /api/v1/provenance/{source_id}` in **<400ms**. `kind:"simulated"` ⇒ violet.

**Error Responses (as SSE `error` or HTTP on initiate):** `401`/`403` · `422 evidence_ungroundable` (abstain — counts toward the ≥95% honesty metric) · `422 metric_unavailable` (a number could not be governed → "unmeasurable as predicted," never fabricated) · `503 provider_degraded` (model failover → lower tier + **visible quality badge**, stream continues) · SSE disconnect → resume via `Last-Event-ID`.

**Validation Rules:** `question` 1–4,000 chars; ingested/source content is treated as data, never instructions (structural evidence-block separation); any quantitative claim must carry a metric-store citation or be marked unmeasurable.

**Rate Limits:** ask class (30/min/user default; viewers included). Budget: **first token <700ms**; provenance resolve **<400ms** (CI-enforced).

**Idempotency Rules:** `POST /api/v1/ask` keyed; a replay returns the same `conversation_id`/`stream_path` (re-attaching to the in-flight or completed stream) rather than launching a duplicate generation.

---

## 12. Provenance (F-14)

**API Purpose:** Resolve any citation to its evidence — the material substrate behind every Claim. Provenance is structural, not a "citations panel": every `source_id`/`chunk_id` in a Claim resolves here.

| | |
|---|---|
| **Endpoints** | `GET /api/v1/provenance/{id}` · `GET /api/v1/provenance/{id}/chain` (full evidence chain for a claim) |
| **Methods** | GET |

**Authentication Rules:** Standard.

**Authorization Rules:** Any member, but **the resolved evidence is itself ACL-trimmed** — a user cannot resolve provenance into a source they can't see; the endpoint returns a withheld marker instead of the content.

**Response Schema:** `{ "source_id", "source_type": "ticket"|"call"|"slack"|"document"|"metric"|"decision", "uri", "excerpt"?: "string", "evidence_weight", "captured_at", "read_principals_satisfied": true }`. For metric-backed claims, returns the governed query + inputs (inspectable join logic for CFO credibility).

**Error Responses:** `403 forbidden` (provenance into unseeable source → withheld marker, not raw 403 of existence) · `404 not_found`.

**Validation Rules:** `id` is a resolvable provenance handle; metric provenance returns the governed call signature, never a free-text number.

**Rate Limits:** read class. Budget **<400ms** (CI-enforced; the Provenance Lens performance budget).

**Idempotency Rules:** N/A (GET).

---

## 13. Agents & Runs (F-06, F-17, F-18, F-19, F-26, F-27 · Conductor and the run lifecycle)

**API Purpose:** Dispatch, observe, approve, and cancel agent work. All eleven agents execute through the same run lifecycle (stateless, checkpointed, replayable, capability-gated). `POST /api/v1/runs` is the universal entry point; the agent and task class are parameters. Conductor (S1) orchestrates multi-agent work via parent/child runs.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/runs` · `GET /api/v1/runs/{id}` · `GET /api/v1/runs/{id}/stream` (SSE) · `GET /api/v1/runs/{id}/steps` · `POST /api/v1/runs/{id}/cancel` · `POST /api/v1/runs/{id}/replay` (audit) · `POST /api/v1/approvals/{runId}` · `GET /api/v1/agents` · `GET /api/v1/agents/{id}` |
| **Methods** | POST, GET |

**Authentication Rules:** Standard. Replay (audit) requires `admin`/`owner` (it reconstructs a run from checkpoints for audit disputes).

**Authorization Rules:**
- Dispatch (`POST /runs`) → `editor`+ for generative/action tasks; viewers may dispatch only read-only research tasks scoped to what they can see.
- **Approval (`POST /approvals/{runId}`) is the Year-1 L2 human gate.** Only a human with authority over the affected object can approve; approving issues the capability token (bound to run/task-class/approval-event, 5-min TTL) that lets the agent's *next* tool call execute. Without an approval, no write/sensitive tool fires (no token exists).
- Agent authority is **never** ambient: the policy engine issues tokens; the governed tool service verifies them cryptographically and **rejects evidence-sourced arguments for sensitive parameters** (injection defense). CI tool-call attack-success target = 0.

**Request Schema:**
- `POST /api/v1/runs` — `{ "agent": "conductor"|"research"|"prd"|"story"|"prioritization"|"roadmap"|"analytics"|"release"|"strategist"|"sentinel"|"archivist", "task_class": "draft_prd"|"write_stories"|"synthesize_feedback"|"rank_candidates"|"sequence_roadmap"|..., "inputs": { "decision_id"?, "prd_id"?, "candidate_set"?, "prompt"? }, "autonomy_target": "L1"|"L2", "stream_id"?: "uuid" }` → `202` + job/run resource.
- `POST /api/v1/approvals/{runId}` — `{ "decision": "approve"|"reject", "edits"?: {...}, "note"?: "string" }`. On approve, the server records the approval event (hash-chained, autonomy log) and issues the capability token.
- `POST /api/v1/runs/{id}/cancel` — none (idempotent).

**Response Schema:**
- Run/job: `{ "id", "agent", "task_class", "status", "autonomy_level", "progress", "parent_run_id"?, "child_run_ids"?, "result_ref"?, "token_cost", "ai_generated": true }`.
- SSE run stream: `step_started | step_completed | progress | run_completed | run_failed` (envelope §2.8).
- `GET /runs/{id}/steps` → ordered step trace (the audit/cost spine; each step has tool calls, inputs/outputs refs, tokens).

**Error Responses:** `403 capability_denied` (agent attempted a tool with no token) · `409 approval_required` (action queued pending human approval) · `403 forbidden` (approver lacks authority) · `422 validation_failed` (bad task_class for agent) · `503 provider_degraded` (Model Gateway failover, run continues at lower tier + badge) · run failure → replayable from checkpoint.

**Validation Rules:** `(agent, task_class)` pairs are validated against each agent's tool manifest and autonomy matrix; Year-1 rejects `autonomy_target` above L2 (`422`); inputs validated per task class. Agent writes must carry `ai_generated=true` + `source_run_id`.

**Rate Limits:** run class (60/min/user default). Autonomous/scheduled work (Overnight PM, backfills) runs on **batch lanes** and does not consume interactive run budgets.

**Idempotency Rules:** `POST /runs` keyed — a replay returns the existing run, never a duplicate dispatch (critical for token-cost integrity). `POST /approvals/{runId}` keyed and bound into the hash-chain so a replayed approval cannot double-issue a token or double-append the autonomy log. `cancel` is idempotent.

**Conductor specifics (F-26):** Conductor dispatch creates a parent run and capability-scoped child runs (Research/PRD/Prioritization/etc.), assembles results, and submits a review package to the human — judgment only. Child runs inherit tenant context and carry their own narrower capability scope.

**Research Agent (F-27)** and other read-only specialists run at L1 (draft) without an approval gate; only writes/external mutations require the L2 token.

---

## 14. PRD Generation (F-24 · Flow 6 — Generate PRD)

**API Purpose:** Turn a committed decision into a build-ready, evidence-native PRD where every sentence is a Claim with citations, including a mandatory contrarian probe ("evidence against"). L1 draft / L2 approve. This is **Flow 6 — Generate PRD**.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/runs` (`agent:"prd"`, `task_class:"draft_prd"`) · `GET /api/v1/runs/{id}/stream` · `POST /api/v1/approvals/{runId}` · `POST /api/v1/prds` (commit on approval) · `GET /api/v1/prds/{id}` |
| **Methods** | POST, GET |

**Authentication Rules:** Standard.

**Authorization Rules:** Dispatch + approve → `editor`+ with authority over the source decision. Approval is the L2 gate; on approve, the PRD is versioned and flagged `ai_generated`/`source_run_id`; edits before approval are recorded as edit-distance (F-20).

**Request Schema:** `POST /runs` inputs — `{ "decision_id": "uuid", "template_id"?: "uuid", "stream_id"?: "uuid" }`. Approval — `{ "decision": "approve", "edits": { "non_goals": "...", ... } }`.

**Response Schema:** Streamed `Claim[]` PRD sections (incl. an explicit "evidence against" section from the contrarian probe). On commit, `POST /api/v1/prds` returns the versioned PRD document (§9 shape), `ai_generated:true`, `source_run_id`, edit-distance summary.

**Error Responses:** `422 evidence_ungroundable` (sections with insufficient evidence marked `inference`, not fabricated) · eval-gate failure → draft never surfaced (F-20 release gate) · `409 approval_required` until human approves · approval declined → draft discarded/iterated.

**Validation Rules:** every prose sentence is a Claim; uncitable → `kind:"inference"` rendered differently; numbers from metric store only; contrarian probe is mandatory (a PRD run without an "evidence against" pass fails its own eval).

**Rate Limits:** run class; interactive generation prioritized over batch lanes for first-token budget.

**Idempotency Rules:** run dispatch + approval + `POST /prds` all keyed; the commit is idempotent on `(decision_id, source_run_id, content_hash)`.

**Human-approval checkpoint:** L2 approval before the PRD is committed (Flow 6).

---

## 15. User Story Generation (F-25 · Flow 7 — Generate Stories)

**API Purpose:** Turn an approved PRD into engineer-grade epics/stories/ACs as evidence-native `Claim[]`. L1/L2 in Year-1 (the L3 push to Jira is V2/F-47). Edit-distance and approval latency measured per `(team, task_class)` — the P4 advocacy instrument.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/runs` (`agent:"story"`, `task_class:"write_stories"`) · `GET /api/v1/runs/{id}/stream` · `POST /api/v1/approvals/{runId}` · `POST /api/v1/epics`, `.../user-stories`, `.../requirements` on commit |
| **Methods** | POST, GET |

**Authentication Rules:** Standard.

**Authorization Rules:** Dispatch → `editor`+; approval → `editor`+/`admin` (Priya's engineering quality bar is encoded as the eval gate + the approval). Writes flagged `ai_generated`/`source_run_id`.

**Request Schema:** `POST /runs` inputs — `{ "prd_id": "uuid", "target_team_id"?: "uuid" }`. Approval — `{ "decision": "approve", "edits"?: {...} }`.

**Response Schema:** Streamed epic tree + stories + ACs as `Claim[]`; on commit, the created hierarchy rows with provenance and edit-distance records. The L3 external push to Jira/Linear/ADO is **not** exposed in Year-1 (that is § 17 / F-47, V2) — Year-1 stays at draft + approve + one-way sync.

**Error Responses:** `422 evidence_ungroundable` · eval-gate failure → blocked · `409 approval_required` · sustained edit-distance >30% after the tuning window → **kill/pivot trigger fires** (scope freeze; surfaced on the quality dashboard, not a per-request error).

**Validation Rules:** ACs non-empty and testable; every prose Claim cited; engineer-grade structure validated against gold standards in the eval harness.

**Rate Limits:** run class.

**Idempotency Rules:** run/approval/commit keyed; story commit idempotent on `(prd_id, source_run_id, content_hash)`.

**Human-approval checkpoint:** L2 approval (Priya's bar).

---

## 16. Decisions, Commit Ceremony & Decision Ledger (F-21, F-28, F-30 · Flow 8 within Prioritization)

**API Purpose:** Make the Decision a first-class, versioned, hash-chained object and provide the ceremonial commit surface. Ledger entries are a **byproduct of actions people already take** (approving a PRD, committing an Arena ranking) — there is **no standalone "log a decision" form**. The commit ceremony is the human judgment; the ledger entry is its byproduct.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/decisions/commit` · `GET /api/v1/decisions/{id}` · `GET /api/v1/decisions/{id}/history` · `POST /api/v1/decisions/{id}/premortem` · `POST /api/v1/decisions/{id}/guards` · GraphQL Decision Sheet lens · `GET /api/v1/audit/verify` |
| **Methods** | POST, GET |

**Authentication Rules:** Standard. Commit requires the human's **typed initial** captured client-side and submitted as a signature payload; ceremonial writes verify the signature server-side before the hash-chain append.

**Authorization Rules:** Commit → `editor`+ with authority over the affected objects. The ledger is **append-only**: `UPDATE`/`DELETE` are revoked at the role level; a "change" appends a new version. Reads are ACL-trimmed.

**Request Schema:**
- `POST /api/v1/decisions/commit` — `{ "context": { "source_action": "prd_approval"|"arena_ranking"|"roadmap_resequence", "source_run_id"?, "prd_id"?, "ranking_id"?, "roadmap_id"? }, "question": "Claim[]", "the_call": "Claim[]", "options"?: [...], "evidence_refs": ["source_id"], "assumptions": [{ "id":"A3", "text":"Claim[]" }], "predicted_impact": { "metric_id", "value", "window" }, "owner_id": "uuid", "dissent"?: [...], "review_date": "date", "signature": { "typed_initial": "SAM", "ts": "..." } }`.
- `POST /decisions/{id}/premortem` — runs synthetic-stakeholder pre-mortem (V2 strategist powers it; Year-1 returns a structured prompt scaffold) → `202` run.
- `POST /decisions/{id}/guards` — `{ "guard": "gate at 5% until A3 verifies", "assumption_id": "A3", "threshold": 0.05 }`.

**Response Schema:** Decision resource: `{ "id", "version", "question": Claim[], "the_call": Claim[], "options", "evidence", "assumptions", "predicted_impact", "owner", "dissent", "review_date", "guards", "ledger": { "row_hash", "prev_hash", "anchored": true, "signature_verified": true } }`.

**Error Responses:** `403 forbidden` · `409 conflict` (commit signature verification failure → rejected) · `422 validation_failed` (missing required ledger fields; predicted_impact metric not governable) · `422 metric_unavailable` (impact metric "unmeasurable as predicted," surfaced not invented).

**Validation Rules:** all prose is `Claim[]`; predicted-impact metric must reference the governed metric store; `review_date` in the future; signature must verify; simulated values inside a decision are gated to `kind:"simulated"` (violet) and never presented as fact.

**Rate Limits:** command class. Commit is ceremonial — low volume, high integrity.

**Idempotency Rules:** Mandatory key, **bound into the hash-chain**: a replayed commit returns the already-appended ledger entry and cannot double-append. This is the strongest idempotency guarantee in the system (financial-grade).

**Audit:** every commit appends `row_hash = H(prev_hash ‖ canonical_payload)`, hourly-anchored to WORM S3, OTel-traced. `GET /api/v1/audit/verify` re-walks the chain and returns the verified head (security-review / M&A-diligence surface).

---

## 17. Prioritization & Roadmaps (F-39, F-40 · Flows 8 & 9)

**API Purpose:** Defensible ranking with explicit trade-offs and counterfactuals (the Arena), and a living, dependency-aware, evidence-linked plan (Horizon). Inputs are grounded in the governed metric store — never invented. Committing a ranking or a re-sequence fires the commit ceremony (§16).

| | |
|---|---|
| **Endpoints** | `POST /api/v1/runs` (`agent:"prioritization"`/`"roadmap"`) · GraphQL Arena lens · GraphQL Horizon lens · `POST /api/v1/decisions/commit` (on commit) · `GET /api/v1/roadmaps/{id}` · `POST /api/v1/roadmaps/{id}/scenarios` |
| **Methods** | POST, GET |

**Authentication Rules:** Standard.

**Authorization Rules:** Run + commit → `editor`+. Read lenses → any member (ACL-trimmed). Scenarios are **simulated** (`kind:"simulated"`, violet) and never committed as fact.

**Request Schema:**
- Prioritization run inputs — `{ "candidate_set": ["object_ref"], "scoring_model_id"?: "uuid", "weights"?: {...} }`.
- Roadmap run inputs — `{ "horizon": "Q3", "constraint_refs": { "capacity", "dependencies", "decisions", "metrics" }, "scenario"?: "string" }`.
- `POST /roadmaps/{id}/scenarios` — `{ "what_if": "Claim[]" }` → simulated scenario (violet).

**Response Schema:** Arena: ranked candidates with per-candidate trade-offs, counterfactuals, and **mandatory contrarian probe**, every input cited from the metric store. Horizon: sequenced plan with dependencies (`Claim[]`), scenarios tagged simulated. Commit returns the ledger entry (§16).

**Error Responses:** `422 metric_unavailable` (a ranking input "unmeasurable," never invented — this is the literal kill of prioritization theater) · circular dependency → `422 validation_failed` surfaced for resolution · capacity data missing → flagged in-body, not guessed · `409 conflict` on commit signature failure.

**Validation Rules:** numbers governed; ties/ambiguity surfaced as trade-offs rather than resolved silently; scenarios force `kind:"simulated"`.

**Rate Limits:** run + graphql classes.

**Idempotency Rules:** run dispatch keyed; commit keyed + hash-chained (§16). Scenario generation is keyed; identical what-ifs return the same simulated result.

**Human-approval checkpoints:** commit ceremony on any committed ranking (Flow 8) or re-sequence that alters decisions (Flow 9).

---

## 18. Feedback Intelligence (F-22, F-23 · Flow 5 — Analyze Feedback)

**API Purpose:** The wedge — every signal clustered, quantified (account/revenue via the metric store), tied to accounts, threaded to decisions. Plus the free **Diagnostic** GTM surface. This is **Flow 5 — Customer Feedback Analysis**.

| | |
|---|---|
| **Endpoints** | GraphQL feedback-cluster lens · `GET /api/v1/feedback/clusters/{id}` · `POST /api/v1/feedback/clusters/{id}/thread` (thread to a decision/artifact) · `POST /api/v1/runs` (`agent:"research"`, `task_class:"synthesize_feedback"`) · `POST /api/v1/diagnostic` · `GET /api/v1/diagnostic/{id}` + `/stream` |
| **Methods** | POST, GET |

**Authentication Rules:** Standard for tenant users. The **Diagnostic** runs under a scoped, time-boxed trial principal established at marketing-led consent; read-only connector scopes only.

**Authorization Rules:** Feedback reads → any member, **ACL-trimmed atoms** ("n withheld"). Threading to a decision → `editor`+ (leads into the commit ceremony). Diagnostic → the trial principal; trial data is isolated and purged per trial policy.

**Request Schema:**
- `POST /feedback/clusters/{id}/thread` — `{ "target": { "type": "decision"|"prd", "id": "uuid" } }`.
- Research run inputs — `{ "cluster_id"?: "uuid", "topic"?: "string", "stream_id"?: "uuid" }`.
- `POST /api/v1/diagnostic` — `{ "connector_refs": ["uuid"], "scope": "read_only" }` → `202` + job + SSE.

**Response Schema:** Clusters with quantification (`Claim[]`, e.g. "Billing friction 3× on enterprise, $1.2M ARR" — every number metric-store-cited), member atoms with provenance, confidence; low-signal clusters flagged `inference`. Diagnostic → a cited findings report + honest coverage estimate and gaps.

**Error Responses:** sparse signal → low-confidence flag (not an error) · `422 metric_unavailable` → cluster shown unquantified but still cited · ACL trim → withheld marker · Diagnostic insufficient data → honest "low coverage" result; connector auth fail → guided retry; job timeout → resumable.

**Validation Rules:** revenue/account numbers from governed metric store only; every claim carries provenance; contrarian probe on synthesis.

**Rate Limits:** graphql + run classes; Diagnostic ingestion on the bulk lane (never blocks live tenants).

**Idempotency Rules:** thread + run + diagnostic POSTs keyed; threading the same cluster to the same target is idempotent.

**Notifications:** a cluster crossing a risk/volume threshold emits a Tide item (§20).

---

## 19. Living Sync & Releases (F-31 one-way Year-1; F-47/F-49 V2 · Flow 10)

**API Purpose:** Push the spec layer to execution tools (Jira/Linear/ADO) with diffs + rationale (Year-1 one-way), and plan releases against readiness gates while arming outcome measurement (V2 endpoint the V1 surfaces build toward). PMOS **syncs, never replaces** the execution tool.

| | |
|---|---|
| **Endpoints** | `POST /api/v1/sync/push` · `GET /api/v1/sync/{id}` · `GET /api/v1/sync/state?object_ref=` · `POST /api/v1/runs` (`agent:"release"`, V2) · `GET /api/v1/releases/{id}` · `POST /api/v1/releases/{id}/readiness` |
| **Methods** | POST, GET |

**Authentication Rules:** Standard. Every external write executes **only** through the governed tool service under a **capability token** (verified cryptographically; evidence-sourced arguments rejected for sensitive params).

**Authorization Rules:** Sync push → `editor`+ and an **L2 approval before any external write** (no token, no write). Year-1 is one-way (spec → tool); bidirectional sync with revert handles + Story L3 push is V2 (F-47). Release planning/readiness → `editor`+/release owner; L3 act-and-notify only for earned task classes (V2 Trust Ladder GA).

**Request Schema:**
- `POST /api/v1/sync/push` — `{ "object_ref": { "type":"epic"|"story", "id":"uuid" }, "target": "jira"|"linear"|"ado", "dry_run"?: true }` → returns the diff + rationale; on confirm (with approval) executes the push via the tool service.
- `POST /api/v1/releases/{id}/readiness` — `{ }` → evaluates readiness gates (V2).

**Response Schema:** Sync: `{ "id", "diff": [...], "rationale": Claim[], "external_ids"?: {...}, "status": "previewed"|"pushed"|"failed", "revert_handle"?: "..." (V2) }`. Release: readiness-gate results, rollout/comms plan (`Claim[]`), armed measurement window (V2).

**Error Responses:** `403 capability_denied` (no token) · `409 approval_required` · external system error → **compensating action surfaced for a human** (Year-1 has no automatic saga rollback; multi-system rollback needs Temporal, V2/TD-4) · readiness gate fail → release blocked · `422 metric_unavailable` (measurement not instrumentable → "unmeasurable as predicted").

**Validation Rules:** external writes carry clean (non-evidence-sourced) arguments only; diffs are previewable (`dry_run`) before any mutation; promised-account notifications (via Commitment Ledger, F-50, V2) respect account ACLs.

**Rate Limits:** command + run classes; external-write throughput bounded by the tool service and target-system rate limits.

**Idempotency Rules:** `sync/push` keyed and idempotent on `(object_ref, target, content_hash)` so a retried push does not create duplicate external items; the tool service dedupes external mutations by idempotency token where the target supports it.

**Security checkpoints (Flow 10):** capability tokens for every external write (CI attack-success target 0); ACL-respecting account notifications; governed, honestly-bounded outcome numbers.

---

## 20. Notifications & The Tide (F-32, F-37)

**API Purpose:** Deliver calm, ranked awareness (the Tide) and the Standing Brief; interrupt only for genuine risk (Vermilion). The Brief is a generated ephemeral narrative re-rendered from the ledger — never stored stale.

| | |
|---|---|
| **Endpoints** | `GET /api/v1/tide/stream` (SSE) · `GET /api/v1/tide` (recent, paginated) · `POST /api/v1/tide/{itemId}/ack` · GraphQL Brief lens · `GET /api/v1/notifications/preferences` · `PATCH /api/v1/notifications/preferences` |
| **Methods** | GET, POST, PATCH |

**Authentication Rules:** Standard; SSE via fetch-stream header or a `tide` stream ticket.

**Authorization Rules:** Each user sees only Tide items and Brief findings their ACL permits; Vermilion (risk/contradiction) items may interrupt. Brief is per-reader (localized, ACL-trimmed) from the one graph.

**Request Schema:** `PATCH /notifications/preferences` — `{ "interrupt_only_vermilion"?: true, "digest_cadence"?: "daily"|"off", "channels"?: ["in_app","email"] }`.

**Response Schema:** Tide SSE events `tide_item | tide_clear | interrupt` (envelope §2.8), each item `{ id, rank, hue: "verdant"|"amber"|"vermilion"|..., finding: Claim[], source_run_id?, uri }`. Brief lens → ranked findings (`Claim[]`), recommended actions inline, freshness badge.

**Error Responses:** SSE disconnect → resume via `Last-Event-ID`; projection stale → **freshness badge** (honest degradation), render miss → last-good + staleness marker.

**Validation Rules:** interruption gated to Vermilion server-side; findings carry provenance; the Brief is never served stale silently — staleness is rendered.

**Rate Limits:** read/graphql classes; one Tide SSE per session.

**Idempotency Rules:** `ack` is idempotent. Brief is a read (no idempotency). Budget: **Brief published by local 6am ≥99.5% of days; cold load to interactive Brief <1.5s** (CI-enforced).

---

## 21. Analytics & Outcome Attribution (F-15, F-48 · the governed numbers + outcome loop)

**API Purpose:** Serve the **governed metric store** (the only legitimate origin of any number in any claim) and the outcome-attribution loop (predicted vs. realized, per team and assumption class). "Numbers are tools, not text."

| | |
|---|---|
| **Endpoints** | `POST /api/v1/metrics/query` (governed tool call) · `GET /api/v1/metrics/catalog` · `GET /api/v1/outcomes/{decisionId}` · `POST /api/v1/runs` (`agent:"analytics"`, V2) · `GET /api/v1/outcomes/scorecards?team_id=` |
| **Methods** | POST, GET |

**Authentication Rules:** Standard. `metrics/query` is reachable by clients for inspection but is the **same governed path** agents use as a tool — there is no ungoverned number anywhere.

**Authorization Rules:** Metric reads → ACL-trimmed by metric sensitivity. Outcome scorecards → `editor`+/`admin`. Arming/closing measurement windows → the Analytics agent (V2) under run authority; humans inspect.

**Request Schema:** `POST /api/v1/metrics/query` — `{ "metric_id": "string", "dimensions"?: {...}, "window": { "from","to" } }`. Returns the value **plus inspectable join logic** (for CFO credibility), uncertainty bands, and freshness.

**Response Schema:** `{ "metric_id", "value", "unit", "join_logic": {...}, "uncertainty": { "low","high" }, "as_of": "ts", "governed": true }`. Outcomes: `{ "decision_id", "predicted": {...}, "realized": {...}, "delta": "+6.2% vs +8% predicted", "method": "holdout"|"diff_in_diff"|"pre_registered", "reported_misses": [...] }`.

**Error Responses:** `422 metric_unavailable` → claim marked "unmeasurable as predicted," **never fabricated**; stale metric → freshness surfaced; ungroundable outcome → honest "unmeasurable," surfaced not hidden.

**Validation Rules:** every number traces to a governed query; outcome methods clear the engineering floor (pre-registered prediction + defined window + holdout/DiD where feasible, honest uncertainty bands, reported misses).

**Rate Limits:** read/run classes; metric tool calls metered (cost + provenance).

**Idempotency Rules:** `metrics/query` is read-like (cacheable by `(metric_id, dims, window)`); outcome-arming runs keyed.

---

## 22. Administration (F-01, F-08, F-16, F-34, F-41, F-42)

**API Purpose:** Operator surfaces for Olivia/owner: connector and member management (covered in §6/§8), procedural-memory governance, consumption metering/billing, compliance/DSAR, and resilience/kill switches.

| | |
|---|---|
| **Endpoints** | `GET/PATCH /api/v1/admin/procedural-memory/{templates|scoring_models|anti_patterns}` · `GET /api/v1/billing/meters` · GraphQL usage lens · `POST /api/v1/dsar` · `GET /api/v1/audit/verify` · `POST /api/v1/ops/killswitch` · `GET /api/v1/ops/status` |
| **Methods** | GET, POST, PATCH |

**Authentication Rules:** Standard; all admin endpoints require `admin`/`owner`; kill switches and DSAR require step-up MFA.

**Authorization Rules:** Procedural-memory edits (templates, scoring models, anti-patterns) → `admin` (Olivia); versioned + revertible. Billing/usage reads → `owner`/finance. DSAR/erasure → `admin`/DPO. Kill switches → `owner`/on-call.

**Request Schema:**
- `PATCH /admin/procedural-memory/templates/{id}` — `{ "content": {...}, "change_note": "string" }` (versioned).
- `POST /api/v1/dsar` — `{ "subject_ref": "...", "action": "access"|"erasure" }` → `202` + job.
- `POST /api/v1/ops/killswitch` — `{ "scope": "tenant"|"agent"|"tool"|"level", "target_id"?: "uuid", "state": "on"|"off", "reason": "string" }`.

**Response Schema:** Procedural-memory resources (versioned); billing meters (append-only, hash-chained) with per-agent/per-task-type consumption mapped to cost; DSAR job → erasure complete ≤24h, audit chain preserved via redacted stubs; killswitch state; ops status with degradation/quality-badge state.

**Error Responses:** `403 forbidden` (role/step-up) · DSAR cascade miss → reconciliation (not silent) · chain-head mismatch on `audit/verify` → integrity alert (sev-1) · killswitch lag → escalation.

**Validation Rules:** procedural edits versioned + revertible; DSAR leaves typed "redacted" stubs so ledger auditability survives; kill-switch scope validated; billing meters are append-only (no edit).

**Rate Limits:** command class; very low ceilings on destructive/ops endpoints.

**Idempotency Rules:** all POSTs keyed; kill-switch toggles idempotent by `(scope, target, state)`; DSAR keyed (one erasure per subject); billing reads are reads.

---

# Part III — Major User-Flow Sequences

Each flow renders the canonical chain **User → Frontend → API Gateway (BFF) → Services → Databases → AI Agents → Response**, with the exact endpoints, the bottleneck, and the control checkpoints. Performance budgets are the §13-spec CI-enforced limits.

## Flow A — Workspace Creation (F-01, F-03)

```
User (Olivia, admin)
  └─▶ Frontend: onboarding wizard {name, region, initial admins}
       └─▶ BFF:  POST /api/v1/workspaces            [Clerk JWT + admin; Idempotency-Key]
            └─▶ Services: Tenancy(allocate org_id+ws_id; SET LOCAL; seed RBAC×ABAC;
            │             register cell/staging stamp)
            │             Identity/Clerk(bind org→IdP, SCIM, MFA editor+)
            │             Outbox(emit workspace.created — same tx)
            │   └─▶ DB Postgres16: INSERT organizations, workspaces (UUIDv7), roles, outbox row (ONE tx)
            │        └─▶ AI Agents: none; memory scopes (org/product/user) initialized empty
            └─◀ Response 201 + workspace → land in "empty-but-speaking" Brief
                ("Connect your first source to begin.")
```
🔻 **Bottleneck:** Clerk org-bind round-trip (dedicated-cell provisioning only at enterprise/Year-2; Year-1 is a single INSERT tx).
❌ **Failure:** `409` double-submit; region unavailable; Clerk bind failure → **whole tx rolls back** (no orphan workspace, no emitted event, because outbox + INSERT share one transaction).
✅ **Approval:** none beyond purchase; admin role required.
🔒 **Security:** Clerk JWT + admin; `org_id`/`ws_id` stamped on row zero → RLS boundary from the first row; residency pinned for later cell placement.

## Flow B — Document Ingestion (F-08→F-12)

```
User (Olivia) ─▶ Frontend: Connectors → OAuth handoff (external) → grant read scope
  └─▶ BFF: POST /api/v1/connectors [admin; Idempotency-Key]; OAuth callback (state+PKCE)
       └─▶ Services ── INGESTION PIPELINE (lanes: live ≤2m / standard ≤15m / bulk) ──
            │  F-08 store credential in SECRET STORE (never in PMOS tables); register webhook/CDC;
            │       capture source ACL read_principals; enqueue backfill (bulk lane)
            │  F-09 normalize → PII screen → INJECTION screen (quarantine:injection_suspect, inert)
            │  F-10 signal extraction (cheap-first cascade; ~70% never touch an LLM)
            │       → FeedbackAtoms / Commitments / DecisionCandidates / RiskSignals
            │  F-11 entity resolution → graph upsert WITH provenance + read_principals
            │  F-12 index fan-out → embeddings (Model Gateway) → Qdrant (point id = chunk id)
            │   └─▶ DB: raw_items, quarantine, *_atoms, accounts, hierarchy, provenance,
            │          document_chunks ↔ Qdrant, outbox per stage
            │        └─▶ AI: screening + extraction + ER models via Model Gateway (metered → agent_runs)
            └─◀ Response: connector "healthy, backfilling"; live items reach the graph ≤2 min;
                findings begin surfacing in Brief/Tide
   (inbound thereafter) ─▶ POST /webhooks/connectors/{provider} [HMAC-verified, dedupe on external_event_id]
```
🔻 **Bottleneck:** LLM-touch in extraction (cheap-first cascade), embedding throughput at backfill scale (bulk lane never blocks live), ER ambiguity.
❌ **Failure:** OAuth scope/token-expiry → re-auth; webhook gap → CDC reconcile; injection false-negative → caught by structural separation + CI red-team; embedding failure → retry; Postgres↔Qdrant drift → nightly reconciler.
✅ **Approval:** none in steady state; quarantine review optional, not a gate on clean content.
🔒 **Security:** secret-store custody (T3); PII screen; injection→inert quarantine (T2 layer 1); source-ACL captured at upsert (load-bearing); `org_id` force-injected into the Qdrant filter.

## Flow C — Ask PMOS (F-13, F-14, F-29)

```
User (any viewer) ─▶ Frontend: ⌘K "why don't we support SSO on Starter?" → intent=Ask
  └─▶ BFF: POST /api/v1/ask → {conversation_id, stream_path}
       then GET /api/v1/ask/{id}/stream  [fetch-stream Authorization OR 60s single-use stream ticket]
       └─▶ Services ── HYBRID GRAPHRAG (F-13) ──
            │  parallel: vector + lexical + typed-graph traversal + GOVERNED METRIC-STORE calls
            │            + ledger lookups
            │  → fusion → cross-encoder rerank → ACL-TRIMMED SELECT (pre-fusion)
            │  → claim-grounded generation (Model Gateway) → groundedness verification
            │   └─▶ DB/Index: document_chunks, hierarchy, decision_ledger, metric store, provenance
            │        └─▶ AI: retrieval-backed Ask; may invoke Research (A2); numbers ONLY from metric store
            └─◀ Response: Claim[] streamed over SSE; each sentence a Provenance Underline;
                click → GET /api/v1/provenance/{id} <400ms. If unseen/insufficient →
                HONEST ABSTENTION ("n sources withheld" / "can't ground this").
```
🔻 **Bottleneck:** first-token latency (frontier model; interactive prioritized over batch lanes); cross-encoder rerank; provenance resolve <400ms at scale.
❌ **Failure:** no groundable evidence → abstain (counts to ≥95% honesty); provider outage → failover lower tier + **visible quality badge**; SSE disconnect → resume via `Last-Event-ID`; metric unavailable → "unmeasurable," never fabricated.
✅ **Approval:** none (read).
🔒 **Security:** stream-ticket auth (never session token in URL); pre-fusion ACL trim; honest existence-of-omission; numbers governed. Budget: **first token <700ms**.

## Flow D — Generate PRD (F-24)

```
User (Sam) ─▶ Frontend: Decision Sheet → "Draft PRD" (or Line "Do", or Conductor)
  └─▶ BFF: POST /api/v1/runs {agent:"prd", task_class:"draft_prd", inputs:{decision_id}, autonomy_target:"L2"}
       → 202 + run; GET /api/v1/runs/{id}/stream (SSE)
       └─▶ Services: Conductor(plan/delegate) → Research(evidence via F-13) →
            │         PRD agent: claim-grounded generation + MANDATORY contrarian probe ("evidence against")
            │   └─▶ DB: documents(+immutable version+chunks), claims, citations, agent_runs, edit_distance
            │        └─▶ AI: PRD(A5), Conductor(S1), Research(A2) via Model Gateway (metered)
            └─◀ Response: Claim[] PRD streamed → queued for review
  User (Sam): edits non-goals (edit-distance logged) →
  └─▶ BFF: POST /api/v1/approvals/{runId} {decision:"approve"}   ✅ L2 GATE → issues capability token
       └─▶ POST /api/v1/prds (commit) → new immutable version, ai_generated=true, source_run_id
```
🔻 **Bottleneck:** retrieval + contrarian pass + first-token under budget.
❌ **Failure:** insufficient evidence → sections `inference`; eval-gate fail → draft never surfaced (F-20); approval declined → discard/iterate.
✅ **Approval:** **L2 approval before commit**.
🔒 **Security:** evidence treated as data not instructions; capability token issued only on approval; numbers governed.

## Flow E — Generate Stories (F-25) + One-Way Sync (F-31)

```
User (Sam) ─▶ Frontend: approved PRD → "Generate stories"
  └─▶ BFF: POST /api/v1/runs {agent:"story", task_class:"write_stories", inputs:{prd_id}}  → 202; SSE
       └─▶ Services: Story agent → epics/stories/ACs as Claim[]; measure edit-distance + approval latency per (team,task)
            └─▶ DB: epics, user_stories, requirements, claims, edit_distance_records
                 └─▶ AI: Story(A6), PRD(A5) upstream
  User (Sam/Priya): review →
  └─▶ BFF: POST /api/v1/approvals/{runId} {approve}   ✅ L2 GATE (Priya's bar)
       └─▶ POST /api/v1/{epics|user-stories|requirements} (commit, ai_generated=true)
  (optional) ─▶ BFF: POST /api/v1/sync/push {object_ref, target:"jira", dry_run:true} → diff+rationale
       └─▶ on approve → governed TOOL SERVICE verifies CAPABILITY TOKEN → external write (one-way, Year-1)
```
🔻 **Bottleneck:** generated-artifact **quality convergence** (kill/pivot trigger: >30% edit-distance after two quarters).
❌ **Failure:** eval-gate fail → blocked; edit-distance breach → scope freeze; external push half-succeeds → **compensating action surfaced for a human** (no auto saga rollback in Year-1).
✅ **Approval:** L2 story approval; L2 approval before any external write (L3 push is V2/F-47).
🔒 **Security:** capability token at the tool service (CI attack-success target 0); evidence-sourced args rejected for sensitive params.

## Flow F — Analyze Feedback (F-22)

```
User (Sam) ─▶ Frontend: Brief finding OR Line "show feedback on billing"
  └─▶ BFF: GraphQL /api/graphql feedback-cluster lens (persisted, complexity-budgeted)
            + governed metric-store calls for quantification
       └─▶ Services: Feedback Intelligence(F-22): cluster FeedbackAtoms → quantify
            │          (account/revenue join via GOVERNED METRIC STORE) → thread to decisions/artifacts
            │          Research(A2) synthesizes on request (contrarian probe)
            │   └─▶ DB: feedback_atoms, accounts, insights, claims, metric store
            │        └─▶ AI: Research(A2); Sentinel(V2) risk threading
            └─◀ Response: clustered, quantified, CITED feedback
                ("Billing friction 3× on enterprise, $1.2M ARR")
  User (Sam): ─▶ POST /api/v1/feedback/clusters/{id}/thread {target: decision} → leads into Flow D/commit
```
🔻 **Bottleneck:** clustering over high-volume atoms; metric-store join latency; relevance rerank.
❌ **Failure:** sparse signal → low-confidence `inference`; ACL trim → "n withheld"; metric join down → unquantified-but-cited.
✅ **Approval:** none to view; threading → commit ceremony.
🔒 **Security:** ACL-trimmed atoms; revenue/account numbers governed only; provenance on every claim.

## Flow G — Generate Roadmap (F-39, F-40)

```
User (Sam/Alex) ─▶ Frontend: Horizon lens "re-sequence Q3 for the billing bet"
  └─▶ BFF: POST /api/v1/runs {agent:"roadmap", task_class:"sequence_roadmap"} → 202; GraphQL Horizon lens
       └─▶ Services: Roadmap(A3): retrieve constraints (capacity, dependencies, decisions, metrics)
            │          → sequence → generate scenarios (Claim[]; simulated = VIOLET)
            │          Prioritization(A4) supplies ranking inputs (governed metrics)
            │   └─▶ DB: roadmaps, releases, dependencies, decision_ledger, metric store
            │        └─▶ AI: Roadmap(A3) + Prioritization(A4) + Conductor
            └─◀ Response → ✅ COMMIT CEREMONY
  User (Sam): review sequencing + dependencies + scenarios → adjust →
  └─▶ POST /api/v1/decisions/commit {context:{source_action:"roadmap_resequence"}, signature}
       → hash-chained ledger entry; affected owners notified
```
🔻 **Bottleneck:** dependency-graph computation; scenario generation over priors; capacity-data joins.
❌ **Failure:** capacity data missing → flagged not guessed; circular dependency → surfaced; scenario over-reach → violet-gated.
✅ **Approval:** commit ceremony on any re-sequence that alters decisions.
🔒 **Security:** metrics governed; simulated scenarios visibly violet; ledger write signed/hash-chained.

---

# Part IV — Risk Analysis (Security · Scalability · Performance)

## 23. Security Risks

Ranked to match the spec's threat model. Each names the API-layer exposure and the control built into this contract.

1. **Cross-tenant exposure (existential, threat #1).** *Risk:* a query, cache entry, vector filter, or projection serves one tenant's data to another. *API controls:* `organization_id` is **never** a client parameter — it is force-injected from TenantContext into both the Postgres RLS context (`SET LOCAL`) and the Qdrant payload filter; any request without a resolvable TenantContext is refused (`403 tenant_context_unresolved`) before data is touched; cursors and idempotency keys are tenant-scoped and signed so they cannot be replayed across tenants; the BFF is the only client-facing surface, so no client reaches Qdrant/Postgres directly. *Residual:* a cache-key collision or a worker running `BYPASSRLS` without an explicit filter — mitigated by code-review gating on workers and **nightly cross-tenant probes (any hit = sev-0)**.
2. **Prompt injection driving an unauthorized action (the AI-native attack, threat #2).** *Risk:* ingested hostile content steers an agent into a write/exfiltration tool call. *API controls:* ingestion screening renders suspect content inert (`quarantine:injection_suspect`); source content is passed only inside delimited typed evidence blocks (structural separation); the governed tool service **rejects evidence-sourced arguments for sensitive parameters**; agents have no ambient authority — a write requires a capability token that exists only after a human L2 approval; tool schemas are the contract. *Target:* CI tool-call attack-success-rate **0**.
3. **Source-credential theft (threat #3).** *Risk:* connector OAuth tokens leak via an API response or log. *API controls:* credentials live in the secret store; **no read endpoint ever returns a credential** (only a reference + non-sensitive scopes/status); inbound webhooks are HMAC-verified, not credential-bearing; OTel traces redact secrets.
4. **Insider / over-privileged access (threat #4).** *Risk:* a member reads beyond their authority. *API controls:* RBAC × ABAC × **pre-fusion source-ACL trim** on every read; provenance resolution is itself ACL-trimmed (you cannot resolve a citation into a source you can't see); step-up MFA on identity-binding, DSAR, and kill switches.
5. **Audit-record tampering (threat #5).** *Risk:* a decision/autonomy/billing record is altered. *API controls:* append-only hash-chained tables (`row_hash = H(prev_hash ‖ payload)`), hourly WORM anchors, `UPDATE`/`DELETE` revoked at the role level; commit idempotency keys are bound into the chain so replays cannot double-append; `GET /api/v1/audit/verify` re-walks the chain.
6. **Supply chain & the public API (Year-3, F-56).** *Risk:* third-party read/write integrations widen the attack surface. *Control (designed-for now):* the same capability-token + governed-tool-service machinery extends to third parties; persisted-query allow-lists and per-integration scopes bound blast radius; `/v1` + `PMOS-Version` let the platform evolve without breaking governance.

**Cross-cutting API hardening:** one error envelope that never leaks tenant data or internal detail; fail-closed validation (unknown filter params → `422`, never silently ignored); idempotency on every POST to prevent duplicate side-effects; rate limits per `(org, user, class)` to bound abuse; SSE stream tickets so a session token never appears in a URL or proxy log.

## 24. Scalability Risks

Design envelope: ~1M users / ~2,500 tenants / 75k editors / 925k viewers / 10⁷ records / 10⁸ chunks per large tenant / ~50M ingestion events/day / ~3M agent tasks/day / ~50k concurrent peak.

- **Viewer read amplification (92% of users).** *Risk:* read-mostly viewers swamp the system. *Control:* viewers hit **precomputed projections / Redis / Postgres** — marginal cost ≈ a cache hit; GraphQL lenses are persisted + complexity-budgeted so no viewer can issue an unbounded query; "unlimited free viewers" is architecturally cheap on purpose.
- **Write fan-out & projection rebuild.** *Risk:* hot tenants overwhelm projection updates. *Control:* transactional outbox + event fan-out; CQRS only where the UX budget demands precomputation (Brief, portfolios, audit timelines); projections rebuildable from the event archive in minutes; Redis Streams Year-1 with the **Kafka/MSK seam** (TD-2) reserved for cell scale.
- **Ingestion spikes / backfills.** *Risk:* a large backfill starves live contradiction detection. *Control:* **three priority lanes** (live ≤2 min / standard ≤15 min / bulk best-effort) — backfills never delay live; cheap-first cascade means ~70% of records never touch an LLM; webhook idempotency dedupes retry storms.
- **Retrieval at 10⁸ chunks.** *Risk:* vector search latency/cost grows with corpus. *Control:* tiered search (coarse pre-filter → rescore → cross-encoder → LLM-select), int8 quantization, **per-tenant reranker heads** (base embedder untouched), Qdrant payload partitioning; the **OpenSearch seam** (TD-8) activates only if exact-match recall drops below ~0.95 on the largest tenant.
- **Agent-task throughput (~3M/day).** *Risk:* run volume saturates the orchestrator. *Control:* NestJS orchestrator + BullMQ with **batch lanes** isolating autonomous work from interactive budgets; stateless checkpointed runs so the **Temporal seam** (TD-4) is a mechanical swap when saga complexity/replay-volume warrants.
- **Tenant blast radius.** *Risk:* one whale tenant degrades the platform. *Control:* `organization_id`-on-every-row makes the **cell migration** (TD-1) an event-replay + router-flip; Year-2 cells cap blast radius ≤4% (25–40 cells × ~60–100 tenants; dedicated cells for enterprise).

## 25. Performance Bottlenecks

The CI-enforced budgets (a regression is a release blocker) and the API-layer cause of each potential breach.

| Surface | Budget | Bottleneck | Mitigation in the contract |
|---|---|---|---|
| Line "Go" search | **<50ms** | Qdrant latency at 10⁸ chunks; ACL trim | int8 quantization + tiered pre-filter; no LLM in the hot path; read-class limits |
| Peek | **<100ms** | projection read | served from precomputed projections / Redis |
| Decision Sheet (cached) | **<150ms** | lens composition | persisted GraphQL + DataLoader batching + complexity budget |
| Ask first token | **<700ms** | frontier-model first token; rerank | interactive prioritized over batch lanes; Model Gateway tiering + failover (visible quality badge) |
| Provenance Lens | **<400ms** | evidence resolution at scale | `GET /provenance/{id}` indexed by `source_id`; chunk id = Qdrant point id (no mapping table) |
| Cold load → interactive Brief | **<1.5s** | narrative re-render from ledger | re-rendered from projections, never stored stale; freshness badge on SLO breach |
| Canvas pan/zoom | **≥60fps** | lens payload size | complexity budget + pagination; client renders graph state, no per-frame API call |

**The three concentrated risks (from the flows):** (1) the **ingestion → retrieval pipeline** (six sequential stages — the longest pole; lane discipline protects live freshness, cheap-first cascade protects cost); (2) **interactive generation latency** (first token under budget while batch lanes serve async — the Model Gateway absorbs provider variance with honest quality badges); (3) **generated-artifact quality convergence** — not latency but a quality problem with a defined kill/pivot trigger (>30% edit-distance after two quarters), which is why the eval harness (F-20) is "built second, not last" and gates every release.

---

# Part V — Cross-Flow Control Summary & Implementation Notes

## 26. Where the four control types concentrate

**Human-approval checkpoints (Year-1 caps autonomy at L1–L2):**
- Every artifact write (PRD §14, Story §15) is **L2** — drafted by the agent, approved by the human via `POST /api/v1/approvals/{runId}`, which is what issues the capability token.
- Every decision/ranking/roadmap change commits via the **commit ceremony** (`POST /api/v1/decisions/commit`, §16) — the typed initial is the judgment; the ledger entry is its byproduct.
- Every external write (Living Sync §19) requires **L2 approval before the governed tool service executes**.
- The **eval harness (F-20)** is a release-blocking approval gate encoding the engineering quality veto.

**Security checkpoints (mapped to the ranked threat model):** cross-tenant (RLS + force-injected `organization_id` + nightly probes) · prompt injection (screening → inert quarantine → structural separation → tool-schema arg rejection → capability confinement, CI target 0) · credential theft (secret-store custody, no credential ever returned) · insider/over-privileged (pre-fusion ACL trim everywhere, step-up MFA) · audit tampering (hash-chained append-only, WORM-anchored, chain-bound idempotency keys).

**Idempotency checkpoints:** every POST keyed (§2.7); ceremonial/financial writes (commit, approval, billing) bind the key into the hash-chain so replays cannot double-append; webhooks dedupe on source event id; run dispatch dedupes so token cost is never double-counted.

**Honest-degradation checkpoints:** stale projection → freshness badge (never silent); model failover → lower tier + visible quality badge (`503 provider_degraded`, stream continues); ungroundable answer → abstention (counts to the ≥95% honesty metric); unmeasurable number → "unmeasurable as predicted," never fabricated.

## 27. Endpoint index (quick reference)

```
AUTH/SESSION   GET /session · POST /stream-tickets · POST /session/logout
ORGS           POST/GET/PATCH /organizations[/{id}] · GET/PUT /organizations/{id}/identity-binding
WORKSPACES     POST/GET/PATCH /workspaces[/{id}] · GET /workspaces
STREAMS/TEAMS  POST/GET/PATCH/DELETE /streams[/{id}] · POST/DELETE /streams/{id}/members[/{userId}]
               POST/GET /lenses[/{id}] · POST /lenses/{id}/share
               GET /workspaces/{id}/members · PATCH /workspaces/{id}/members/{userId}
USERS          GET/PATCH /users/me · GET /users/{id} · GET /workspaces/{id}/users
CONNECTORS     POST/GET/DELETE /connectors[/{id}] · GET /connectors/{id}/{health|coverage}
               POST /connectors/{id}/backfill · POST /connectors/oauth/callback
               POST /webhooks/connectors/{provider}
INGESTION      GET /ingestion/quarantine · POST /ingestion/quarantine/{itemId}/release
HIERARCHY      POST/GET/PATCH/DELETE /products·features·epics·user-stories·requirements[/{id}]
DOCUMENTS      POST/GET /documents[/{id}] · GET /documents/{id}/versions[/{versionId}]
PRD            POST /runs(prd) · POST /approvals/{runId} · POST/GET /prds[/{id}]
SEARCH         GET /search                                         (Go <50ms)
ASK/CHAT       POST /ask · GET /ask/{id}/stream · GET /conversations/{id}    (first token <700ms)
PROVENANCE     GET /provenance/{id} · GET /provenance/{id}/chain   (<400ms)
AGENTS/RUNS    POST/GET /runs[/{id}] · GET /runs/{id}/{stream|steps} · POST /runs/{id}/{cancel|replay}
               POST /approvals/{runId} · GET /agents[/{id}]
DECISIONS      POST /decisions/commit · GET /decisions/{id}[/history]
               POST /decisions/{id}/{premortem|guards} · GET /audit/verify
PRIORITIZATION POST /runs(prioritization) · GraphQL Arena lens
ROADMAPS       POST /runs(roadmap) · GET /roadmaps/{id} · POST /roadmaps/{id}/scenarios · GraphQL Horizon
FEEDBACK       GraphQL feedback lens · GET /feedback/clusters/{id} · POST /feedback/clusters/{id}/thread
               POST /diagnostic · GET /diagnostic/{id}[/stream]
SYNC/RELEASE   POST /sync/push · GET /sync/{id} · GET /sync/state · GET /releases/{id}
               POST /releases/{id}/readiness · POST /runs(release, V2)
NOTIFICATIONS  GET /tide[/stream] · POST /tide/{id}/ack · GraphQL Brief lens
               GET/PATCH /notifications/preferences
ANALYTICS      POST /metrics/query · GET /metrics/catalog · GET /outcomes/{decisionId}
               GET /outcomes/scorecards · POST /runs(analytics, V2)
ADMIN          GET/PATCH /admin/procedural-memory/{kind}[/{id}] · GET /billing/meters
               POST /dsar · POST /ops/killswitch · GET /ops/status
JOBS (grammar) GET /jobs/{id} · GET /jobs/{id}/stream · POST /jobs/{id}/cancel
GRAPHQL        POST /api/graphql   (persisted only in prod; cost ≤1000, depth ≤8)
```

## 28. Forward-compatibility seams (must not be foreclosed)

- **Tenancy (TD-1):** `organization_id` on every row + force-injection makes the Year-2 **cell** migration an event-replay + router-flip, not a rewrite. No client API changes.
- **Events (TD-2):** the outbox is the invariant; **Kafka/MSK** replaces Redis Streams transparently behind the relay worker at cell scale.
- **Agent authority (TD-3):** the capability-token model is the contract today (L0–L2 via approval endpoints + `ai_generated` flags); **L3/L4 TTL + two-person-rule** layer on at Trust Ladder GA without changing the `POST /approvals` surface.
- **Orchestration (TD-4):** stateless, checkpointed, replayable runs make the **Temporal** swap mechanical; `POST /runs` + the job grammar are unchanged.
- **Lexical engine (TD-8):** the `GET /search` contract is engine-agnostic; **OpenSearch** can join behind it if exact-match recall demands it.
- **Knowledge model (TD-6):** the relational hierarchy is the system of record; the **generic bitemporal typed graph** layers over it (Year-2) and is projected through the same GraphQL lens contract.
- **Public Platform/API (F-56, Year-3):** `/v1` + `PMOS-Version` + persisted queries + capability tokens are the same machinery third parties will use; no re-architecture required.

---

*This document specifies Year-1-authoritative API behavior per the spec's rule of precedence. Endpoints whose full capability is a V2/Year-3 target (bidirectional Living Sync + Story L3, Release/Launch Control, the Analytics outcome agent, the public Platform API) are labeled so engineering can build the Year-1 seam without building the end-state prematurely. The contract above — protocols, auth, authorization, schemas, errors, validation, rate limits, and idempotency — is specified at the granularity needed to begin implementation across frontend, backend, and AI engineering tracks without ambiguity.*
