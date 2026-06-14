# PMOS — API Inventory

### Every Feature Mapped to the APIs Required to Implement It

**Source of truth:** `PMOS_MASTER_SPEC_Final.md` (Constitution v1.0) · `Feature_Inventory.md` (F-01…F-61) · `User_Flows.md` · `API_Design.md` (API Constitution v1.0)
**Scope:** Every **Foundation** (F-01…F-21), **MVP** (F-22…F-34), and **V1** (F-35…F-42) feature.
**Purpose:** Give engineering teams a single artifact that answers, for each feature: *exactly which APIs are required to build it, what each API's contract is, which backend services / database entities / AI agents / external integrations it touches, and what it depends on.*

---

## 0. How to read this document

This is a build-planning map, not a tutorial. It is organized in two parts.

**Part I — Feature → API mapping.** One section per in-scope feature, in the fixed template below. Every API named is a **BFF endpoint** (the BFF is the only client-facing surface; no client calls a domain service, the Model Gateway, Qdrant, or Postgres directly). Cross-cutting contracts (auth, errors, pagination, idempotency, the `Claim[]` wire type) are defined once in §0.2–§0.4 and referenced — each feature states only its deltas.

**Part II — Cross-cutting matrices.** The API Dependency Matrix, Feature-to-API Mapping, Agent-to-API Mapping, and Service-to-API Mapping, followed by the classification of Shared / Critical / High-traffic / AI-heavy APIs.

### 0.1 Per-feature template

> **Feature Name** · **Purpose** · **APIs Required** (each with *Endpoint · Method · Request Schema · Response Schema · Auth Requirements*) · **Backend Services Used** · **Database Entities Used** · **AI Agents Used** · **External Integrations Used** · **Dependencies**

### 0.2 Universal auth baseline (assumed everywhere unless a feature overrides it)

Every authenticated request carries `Authorization: Bearer <Clerk-JWT>`, JWKS-verified at the BFF. From the JWT the BFF resolves a **TenantContext** `(organization_id, workspace_id, user_id, roles[], abac_attrs)` and opens a tenant-scoped transaction (`SET LOCAL app.current_org_id`). A request that cannot resolve a TenantContext is refused `403 tenant_context_unresolved` before any data is touched. Passwords never touch any PMOS endpoint. RBAC roles: `viewer` (free, unlimited, read-only) · `editor` (PM: draft/commit) · `admin` (Olivia: connectors, members, procedural memory, compliance) · `owner` (billing, residency, kill switches). Authorization on every request = **RBAC × ABAC × pre-fusion source-ACL trim**. SSE consumed via native `EventSource` uses a single-use 60s **stream ticket**, never the session token. Below, "Auth Requirements" states the *delta* from this baseline (role floor, ABAC narrowing, step-up MFA, capability token, or public principal).

### 0.3 Universal wire conventions

`Claim[]` is the protocol type for every AI-generated prose field: `Claim = {text, citations: Citation[], kind: "fact"|"inference"|"simulated", confidence: 0..1}`; `Citation = {source_id, chunk_id?, uri?, evidence_weight}`. One error envelope everywhere (`{error:{code,message,status,request_id,trace_id,meta,retryable}}`). Cursor-only pagination (`?limit&cursor` → `{data, page:{next_cursor,has_more}}`); `COUNT(*)` per page forbidden. Two-key versioning: `/api/v1` (structural) + `PMOS-Version` date header (behavioral). Every POST carries a mandatory `Idempotency-Key`; ceremonial/financial writes bind the key into the hash-chain. `organization_id` / `workspace_id` are **never** client parameters — force-injected from TenantContext.

### 0.4 The async-job grammar (one grammar for everything slow)

Exports, imports, backfills, simulations, the Diagnostic, and every agent run share one grammar: `POST …` → `202 + {job:{id,status,progress,links}}`; `GET /api/v1/jobs/{jobId}`; `GET /api/v1/jobs/{jobId}/stream` (SSE progress); `POST /api/v1/jobs/{jobId}/cancel`. `status ∈ {queued,running,succeeded,failed,cancelled,cancelling}`. Agent runs are the canonical instance: `POST /api/v1/runs` → `202` + a job whose `id` is the `agent_runs.id`. Where a feature says "async-job grammar," these four endpoints are implied and not re-listed.

---

# Part I — Feature → API Mapping

# 1. Foundation (F-01 … F-21)

*The platform substrate. Nothing in MVP/V1 ships until these exist (the spec's §20 critical path).*

---

## F-01 · Multi-Tenancy & Row-Level Security (RLS) Core

**Purpose:** Shared-database, shared-schema tenancy enforced by PostgreSQL Row-Level Security. `organization_id` **and** `workspace_id` on every content row; `FORCE ROW LEVEL SECURITY`; per-request `SET LOCAL app.current_org_id` inside a tenant-scoped transaction. The data layer refuses any query lacking a TenantContext. This is the existential cross-tenant-leak defense (threat #1) and the seam that keeps the Year-2 cell migration to an event-replay + router-flip.

F-01 ships almost no endpoints of its own — it is enforcement machinery woven into **every** endpoint via the TenantContext baseline (§0.2). Its directly-attributable surface is the org/tenancy boundary and the operator kill-switch scope.

**APIs Required**

1. **Create / read / update an Organization (the RLS boundary)**
   - *Endpoint:* `POST /api/v1/organizations` · `GET /api/v1/organizations/{orgId}` · `PATCH /api/v1/organizations/{orgId}`
   - *Method:* POST, GET, PATCH
   - *Request Schema:* `POST {name, residency_region, owner_email, plan:"platform_fee"|"consumption"|"hybrid"}` · `PATCH {name?, default_mfa_policy?, default_pmos_version?}`
   - *Response Schema:* `{id, name, residency_region, plan, created_at, identity_binding:{idp,scim_enabled}, kill_switch_state}`
   - *Auth Requirements:* Create → platform-provisioning principal or `owner` (very low ceiling). Read/PATCH → `admin`/`owner` **of that org only** (RLS guarantees no cross-org read). Residency is write-once.

2. **Tenant-scoped session resolution (force-injection check on every request)**
   - *Endpoint:* `GET /api/v1/session`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* `{user, organization:{id,name,residency_region}, workspace, authority:{roles,abac,mfa_satisfied}, pmos_version}`
   - *Auth Requirements:* valid Clerk JWT resolving to a TenantContext; otherwise `403 tenant_context_unresolved`.

3. **Kill-switch scope (org/tenant blast-radius control — shared with F-42)**
   - *Endpoint:* `POST /api/v1/ops/killswitch`
   - *Method:* POST
   - *Request Schema:* `{scope:"tenant"|"agent"|"tool"|"level", target_id?, state:"on"|"off", reason}`
   - *Response Schema:* `{scope, target_id?, state, applied_at}`
   - *Auth Requirements:* `owner`/on-call + **step-up MFA**.

**Backend Services Used:** Tenancy service (org/ws allocation, `SET LOCAL`, RLS policy), Identity binding (Clerk), BFF (force-injection of `organization_id` into Postgres RLS + Qdrant payload filter).
**Database Entities Used:** `organizations`, `workspaces`, `roles`/role assignments, `outbox`; RLS policies on **all** content tables; nightly cross-tenant probe audit rows.
**AI Agents Used:** None (platform).
**External Integrations Used:** Clerk (identity binding for the org).
**Dependencies:** PostgreSQL 16 provisioned (bedrock — no upstream feature dependency).

---

## F-02 · Identity & Access (Clerk Integration)

**Purpose:** Clerk as canonical identity provider — SSO (SAML/OIDC), MFA for editor+ roles, SCIM (enterprise), JWKS-verified JWTs. Passwords never touch PMOS. Two principals modeled distinctly: a human's authority is a session claim (RBAC × ABAC × source-ACL trim); `auth_provider` is retained only as an identity-source label, never a credential store.

**APIs Required**

1. **Establish / inspect the session**
   - *Endpoint:* `GET /api/v1/session` · `POST /api/v1/session/logout`
   - *Method:* GET, POST
   - *Request Schema:* `session` none; `logout` `{}` + `Idempotency-Key`
   - *Response Schema:* session resource (see F-01 #2); logout `{ok:true}`
   - *Auth Requirements:* valid Clerk JWT (JWKS-verified). SCIM-deprovisioned users are blocked at JWT verification independent of token expiry.

2. **Mint SSE stream tickets (so the session token never enters a URL)**
   - *Endpoint:* `POST /api/v1/stream-tickets`
   - *Method:* POST
   - *Request Schema:* `{stream_kind:"ask"|"run_progress"|"tide"|"job_progress", resource_id?}` + `Idempotency-Key`
   - *Response Schema:* `201 {ticket, expires_at, single_use:true}`
   - *Auth Requirements:* any authenticated principal, but only for stream kinds the caller is authorized to consume (a viewer cannot mint a `run_progress` ticket for a run it doesn't own). TTL fixed server-side at 60s.

3. **Bind the org to its IdP (federated identity)**
   - *Endpoint:* `GET /api/v1/organizations/{orgId}/identity-binding` · `PUT /api/v1/organizations/{orgId}/identity-binding`
   - *Method:* GET, PUT
   - *Request Schema:* `PUT {idp:"saml"|"oidc", metadata_url, scim_enabled}`
   - *Response Schema:* `{idp, scim_enabled, bound_at}`
   - *Auth Requirements:* `owner` + **step-up MFA** (re-auth claim in the JWT).

4. **Read user projections (SCIM-synced identities)**
   - *Endpoint:* `GET /api/v1/users/me` · `GET /api/v1/users/{id}` · `GET /api/v1/workspaces/{wsId}/users`
   - *Method:* GET
   - *Request Schema:* none (list is cursor-paginated)
   - *Response Schema:* `{id, display_name, email?, auth_provider, roles, preferences, created_at}` (email/roles trimmed by viewer authority)
   - *Auth Requirements:* `me` → self; other users → any member (display fields), email/roles to `admin`+. User create/deactivate is **not** a PMOS endpoint — it happens via SCIM in the IdP and syncs through Clerk.

**Backend Services Used:** Identity service (Clerk JWKS verification, SCIM sync, MFA enforcement), Session/Authority resolver (RBAC × ABAC), Stream-ticket minter.
**Database Entities Used:** `users` (Clerk projections), `roles`/role assignments, `organizations.identity_binding`, ABAC attribute tables.
**AI Agents Used:** None (platform).
**External Integrations Used:** **Clerk** (SSO SAML/OIDC, MFA, SCIM, JWKS).
**Dependencies:** F-01 (tenant context binds to identity).

---

## F-03 · Core Persistence Schema & Product Hierarchy

**Purpose:** PostgreSQL 16 as system of record. Explicit first-class hierarchy `Organization → Workspace → {Products → Features → Epics → User Stories → Requirements}`, plus Roadmaps/Releases, Feedback/Interviews/Insights, and Documents (immutable versions + chunks). UUIDv7 keys (double as Qdrant point IDs). Soft delete (`deleted_at`, 30-day trash → purge = GDPR path). Polymorphism `(context_type, context_id)` only for genuinely to-anything edges.

**APIs Required**

1. **Workspace lifecycle (Flow A — stamps `organization_id` + `workspace_id` on row zero)**
   - *Endpoint:* `POST /api/v1/workspaces` · `GET /api/v1/workspaces/{wsId}` · `PATCH /api/v1/workspaces/{wsId}` · `GET /api/v1/workspaces`
   - *Method:* POST, GET, PATCH
   - *Request Schema:* `POST {name, region, initial_admin_emails[], seed_stream_name?}` · `PATCH {name?, default_stream_id?}`
   - *Response Schema:* `201 {id, organization_id, name, region, created_at, brief_state:"empty_but_speaking", next_action:{code:"connect_first_source", label}}`
   - *Auth Requirements:* create/patch → `admin`+; read → any member whose ABAC permits.

2. **Product-hierarchy CRUD (products / features / epics / user-stories / requirements)**
   - *Endpoint:* `POST/GET/PATCH/DELETE /api/v1/products/{id?}` · `.../features/{id?}` · `.../epics/{id?}` · `.../user-stories/{id?}` · `.../requirements/{id?}`
   - *Method:* POST, GET, PATCH, DELETE
   - *Request Schema (representative — user-stories):* `POST {epic_id, title, body:string|Claim[], acceptance_criteria:Claim[][], stream_id?}` · `PATCH` partial fields (edits to agent-authored artifacts recorded as edit-distance)
   - *Response Schema:* typed resource with hierarchy refs, `claims` for AI prose, `freshness` badge, `ai_generated`/`source_run_id` when applicable
   - *Auth Requirements:* read → any member, source-ACL-trimmed; create/edit → `editor`+; DELETE is soft-delete; `UPDATE`/`DELETE` role-revoked where the audit fabric applies.

3. **Documents & immutable versions**
   - *Endpoint:* `POST/GET /api/v1/documents/{id?}` · `GET /api/v1/documents/{id}/versions` · `GET /api/v1/documents/{id}/versions/{versionId}`
   - *Method:* POST, GET
   - *Request Schema:* `POST {type, title, sections:[{heading, content:string|Claim[]}], stream_id?}`
   - *Response Schema:* `{id, type, title, sections, version:{id,n,immutable}, ai_generated?, source_run_id?, freshness}`
   - *Auth Requirements:* read → any member (ACL-trimmed); create/edit → `editor`+. Immutable versions cannot be edited — a change creates a new version; version creation idempotent on `(document_id, content_hash)`.

**Backend Services Used:** Hierarchy/domain service, Document/versioning service, Tenancy (RLS context), Outbox (state-change events).
**Database Entities Used:** `products`, `features`, `epics`, `user_stories`, `requirements`, `roadmaps`, `releases`, `feedback`, `interviews`, `insights`, `documents`, `document_versions`, `document_chunks`, polymorphic `(context_type, context_id)` edge tables (comments/tags/links/conversations), `outbox`.
**AI Agents Used:** None directly (schema is AI-aware by design; agents write through it).
**External Integrations Used:** None (Supabase Storage for user-facing blobs / S3 for archives at the storage layer).
**Dependencies:** F-01.

---

## F-04 · Transactional Outbox & Event Backbone

**Purpose:** Transactional outbox as the invariant — no event emitted without the state change committed in the same transaction. Redis Streams transport (Year-1) with a relay worker; Kafka/MSK-swappable seam at cell scale. Event fan-out drives projections and async work.

F-04 is internal infrastructure with **no dedicated client-facing endpoints**. It is exercised implicitly by every mutating endpoint (each Postgres write commits its `outbox` row in the same transaction) and observed by clients only indirectly through outbox-driven SSE invalidation and projection freshness.

**APIs Required**

1. **Outbox-driven SSE invalidation / progress (consumed, not a distinct route)** — the relay worker's fan-out is what powers `GET /api/v1/runs/{id}/stream`, `GET /api/v1/tide/stream`, `GET /api/v1/jobs/{jobId}/stream`, and projection-staleness freshness badges. These routes are owned by their respective features (F-17, F-32/F-37, async grammar) but **depend on F-04** for delivery.
   - *Endpoint:* (no new endpoint; F-04 is the transport beneath the SSE routes above)
   - *Method:* —
   - *Request Schema:* —
   - *Response Schema:* SSE envelope `id:/event:/data:` (§0.3) emitted from fanned-out outbox events
   - *Auth Requirements:* inherited from the consuming SSE route.

**Backend Services Used:** Outbox relay worker, Redis Streams transport, projection builders (CQRS where the UX budget demands precomputation).
**Database Entities Used:** `outbox`, projection/materialized-view tables, event archive.
**AI Agents Used:** None (platform).
**External Integrations Used:** None Year-1 (Kafka/MSK is the named Year-2 seam).
**Dependencies:** F-03, Redis provisioned.

---

## F-05 · API Gateway / BFF & Three-Protocol Contract

**Purpose:** The BFF as the single client-facing surface. Three-protocol rule (mutates → REST; composes ≥3 resources → GraphQL lens; server-push → SSE). Carries the `Claim[]` wire type, one error format, cursor-only pagination, mandatory `Idempotency-Key`, two-key versioning, and the async-job grammar. Ships with a dev fixture implementing the exact contract.

F-05 *is* the contract every other feature's endpoints are expressed in; its directly-attributable surface is the cross-cutting machinery and the GraphQL entry point.

**APIs Required**

1. **The single GraphQL lens endpoint (all multi-resource read lenses route here)**
   - *Endpoint:* `POST /api/graphql`
   - *Method:* POST
   - *Request Schema:* persisted-query hash (+ variables) in production; arbitrary query text only in dev. Complexity budget cost ≤ 1,000, depth ≤ 8, evaluated before execution.
   - *Response Schema:* lens-shaped JSON; AI prose fields resolve to `Claim[]`; quantitative fields resolve from the governed metric store; connections use keyset pagination (`edges`, `pageInfo.endCursor`, `pageInfo.hasNextPage`).
   - *Auth Requirements:* standard; per-viewer ACL trim applied inside resolvers; complexity counts against the `graphql` rate-limit class.

2. **The async-job grammar (shared by every slow feature — §0.4)**
   - *Endpoint:* `GET /api/v1/jobs/{jobId}` · `GET /api/v1/jobs/{jobId}/stream` · `POST /api/v1/jobs/{jobId}/cancel`
   - *Method:* GET, POST
   - *Request Schema:* `cancel` none + `Idempotency-Key`
   - *Response Schema:* `{job:{id,status,progress:{pct,stage,detail?:Claim[]}, result_ref|error}}`; stream emits run/job progress events
   - *Auth Requirements:* caller must own or be able to view the job's `resource_id`.

3. **Behavioral version negotiation (cross-cutting on every response)**
   - *Endpoint:* (header on all routes) `PMOS-Version` request header → echoed in the `PMOS-Version` response header
   - *Method:* — (all)
   - *Request Schema:* `PMOS-Version: <date>` (optional; omission pins the account default)
   - *Response Schema:* resolved `PMOS-Version` echoed
   - *Auth Requirements:* baseline.

**Backend Services Used:** BFF (protocol routing, JWKS verify, TenantContext resolution, idempotency cache, rate limiting, error envelope), GraphQL gateway (persisted-query registry, complexity evaluator, DataLoader batching), job/async orchestration shim.
**Database Entities Used:** idempotency-key cache (`(org,user,method,path,key)` → cached response, 24h), persisted-query registry, `jobs`/`agent_runs` (job state).
**AI Agents Used:** None directly (carries `Claim[]` payloads produced by AI features).
**External Integrations Used:** None.
**Dependencies:** F-01, F-03.

---

## F-06 · AI Schema Spine (Agents, Runs, Conversations, Metering)

**Purpose:** AI as first-class in the schema — `ai_agents` (with `model` binding), `conversations`, `messages` (with `tool_calls`), `agent_runs` (token-metered) + `agent_run_steps` (step trace). The audit & cost spine every agent and metering feature reads from.

**APIs Required**

1. **Agent registry (read the eleven agents and their bindings)**
   - *Endpoint:* `GET /api/v1/agents` · `GET /api/v1/agents/{id}`
   - *Method:* GET
   - *Request Schema:* none (list cursor-paginated)
   - *Response Schema:* `{id, name, role_charter, model, autonomy_matrix, tool_manifest_ref, kpis}`
   - *Auth Requirements:* any member (read); `admin` for full manifest detail.

2. **Run record + step trace (the metered audit spine)**
   - *Endpoint:* `GET /api/v1/runs/{id}` · `GET /api/v1/runs/{id}/steps`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* run `{id, agent, task_class, status, autonomy_level, progress, parent_run_id?, child_run_ids?, result_ref?, token_cost, ai_generated:true}`; steps → ordered trace (tool calls, inputs/outputs refs, tokens)
   - *Auth Requirements:* owner/viewer of the run; replay/audit detail → `admin`/`owner`.

3. **Conversation history (messages + tool_calls)**
   - *Endpoint:* `GET /api/v1/conversations/{id}`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* `{id, messages:[{role, content:Claim[]|string, tool_calls?}], created_at}`
   - *Auth Requirements:* conversation participant; ACL-trimmed.

**Backend Services Used:** Agent registry service, Run/metering service (token accounting), Conversation service.
**Database Entities Used:** `ai_agents`, `conversations`, `messages` (`tool_calls`), `agent_runs` (token meters), `agent_run_steps`.
**AI Agents Used:** None directly (this is the schema the agents *write into*; all eleven agents read/write here at runtime).
**External Integrations Used:** None directly (the Model Gateway, F-07, populates token meters).
**Dependencies:** F-03.

---

## F-07 · Model Gateway

**Purpose:** Single gateway fronting all frontier-model calls with ZDR contracts and no cross-tenant training by default. Per-task tiered routing (frontier / mid / small / embedding); default agent model `claude-sonnet-4-6`; OpenAI `text-embedding-3-large` @ 3072 dims for vectors. Batch lanes for autonomous work; provider failover degrades Ask to a lower tier with a **visible quality badge**.

F-07 is an **internal service reachable only from the BFF/agent runtime, never from a client directly**. Its behavior surfaces through other features' endpoints (Ask, runs, ingestion) as a tier/quality badge and through ops status.

**APIs Required**

1. **Degradation / quality-badge surface (observed on Ask, runs, ops status)**
   - *Endpoint:* `GET /api/v1/ops/status` (gateway/degradation state) — and the `503 provider_degraded` error + quality badge emitted inline on `GET /api/v1/ask/{id}/stream` and `GET /api/v1/runs/{id}/stream`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* `{model_gateway:{tier, degraded:bool, quality_badge?}, ...}`; inline SSE carries the badge while the stream continues
   - *Auth Requirements:* `ops/status` → `admin`/`owner`; the inline badge inherits the consuming stream's auth.

**Backend Services Used:** Model Gateway (routing/tiering, ZDR contracts, failover, batch lanes), Token-metering hook into `agent_runs`.
**Database Entities Used:** `ai_agents.model` (binding), `agent_runs`/`agent_run_steps` (token cost), routing-policy config.
**AI Agents Used:** All eleven agents call the gateway at runtime (no agent is the *subject* of F-07; F-07 serves them).
**External Integrations Used:** **Anthropic Claude** (`claude-sonnet-4-6` default) · **OpenAI** (`text-embedding-3-large` @ 3072d).
**Dependencies:** F-06 (model binding lives in `ai_agents`).

---

## F-08 · Connector SDK & Source Connectors (≥6)

**Purpose:** A connector SDK plus six initial connectors (Zendesk/Jira + Notion/Confluence as the value-proof minimum; Slack/Linear; Gong/Salesforce as honestly-surfaced coverage upgrades). Connectors expose webhooks/CDC and source ACL data for ≤2-min freshness and ≤1h ACL reconciliation.

**APIs Required**

1. **Authorize / manage a connector**
   - *Endpoint:* `POST /api/v1/connectors` · `GET /api/v1/connectors` · `GET /api/v1/connectors/{id}` · `DELETE /api/v1/connectors/{id}`
   - *Method:* POST, GET, DELETE
   - *Request Schema:* `POST {provider:"zendesk"|"jira"|"notion"|"confluence"|"slack"|"linear"|"gong"|"salesforce", auth_method:"oauth", lane_policy?:"live"|"standard"|"bulk", scopes:["read:tickets",...]}` → returns an OAuth authorize URL
   - *Response Schema:* `{id, provider, status:"pending_auth"|"healthy"|"degraded"|"expired", scopes, lane_policy, last_sync_at, acl_reconciled_at}` (**credentials never returned** — only a secret-store reference + non-sensitive metadata)
   - *Auth Requirements:* `admin`. Requested scopes must be **read** scopes.

2. **OAuth callback (redirect-back)**
   - *Endpoint:* `POST /api/v1/connectors/oauth/callback`
   - *Method:* POST
   - *Request Schema:* `{state, code, ...}` validated by `state` + PKCE
   - *Response Schema:* connector resource with `status` advancing to `healthy`
   - *Auth Requirements:* validated by `state` + PKCE (not a standard Clerk-authed body); credential stored in the secret store, never in PMOS tables.

3. **Coverage & health**
   - *Endpoint:* `GET /api/v1/connectors/{id}/coverage` · `GET /api/v1/connectors/{id}/health`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* coverage `{customer_voice_coverage:0.41, upgrade_hint:{add:"gong", would_raise_to:0.78}}` (honest upgrade framing, never a hard requirement); health → freshness per lane, webhook gap status, backfill progress
   - *Auth Requirements:* `admin`/`owner`.

4. **Backfill (async-job grammar, bulk lane)**
   - *Endpoint:* `POST /api/v1/connectors/{id}/backfill`
   - *Method:* POST → `202`
   - *Request Schema:* `{since?, lane:"bulk"}` + `Idempotency-Key`
   - *Response Schema:* `202 {job:{...}}`; backfill never consumes the live lane's freshness budget
   - *Auth Requirements:* `admin`.

5. **Inbound webhooks (untrusted data, never instructions)**
   - *Endpoint:* `POST /webhooks/connectors/{provider}`
   - *Method:* POST
   - *Request Schema:* provider-shaped body; PMOS normalizes
   - *Response Schema:* `200` (deduped on `(provider, external_event_id)`)
   - *Auth Requirements:* **not** Clerk-authed — HMAC-verified against a per-connector secret + tenant-routing token; unverified → `401`, dropped before the pipeline.

**Backend Services Used:** Connector SDK/runtime, OAuth handler (state+PKCE), Secret store custody, Webhook receiver (HMAC verify, dedupe), Ingestion enqueue (lane router).
**Database Entities Used:** `connectors`, `connector_secrets` (reference only), `raw_items`, webhook dedupe keys `(provider, external_event_id)`, `read_principals` captured per source.
**AI Agents Used:** None directly (feeds the enrichment pipeline F-10/F-11).
**External Integrations Used:** **Zendesk, Jira, Notion, Confluence, Slack, Linear** (value set) · **Gong, Salesforce** (coverage upgrades) — all read-only; OAuth/webhooks/CDC.
**Dependencies:** F-03, F-04.

---

## F-09 · Ingestion Pipeline — Normalization, PII & Injection Screening

**Purpose:** The Knowledge-plane front half: normalize → PII screen → prompt-injection screen (`quarantine:injection_suspect`, rendered inert). Three priority lanes (live ≤2 min / standard ≤15 min / bulk best-effort). "Ingested content is hostile until proven otherwise." Layer 1 of injection defense (threat #2).

F-09 is an **internal, event-chained pipeline stage, not separately client-callable.** Its only client-facing surface is quarantine review.

**APIs Required**

1. **Review / release the injection-&-PII quarantine**
   - *Endpoint:* `GET /api/v1/ingestion/quarantine` · `POST /api/v1/ingestion/quarantine/{itemId}/release`
   - *Method:* GET, POST
   - *Request Schema:* list is cursor-paginated; `release {}` + `Idempotency-Key`
   - *Response Schema:* `[{id, reason:"injection_suspect"|"pii", source_ref, quarantined_at}]` (content rendered **inert**, never executed); release → item re-queued for downstream stages
   - *Auth Requirements:* `admin`.

**Backend Services Used:** Ingestion pipeline (normalization), PII screener, Injection screener (quarantine), Lane router (live/standard/bulk).
**Database Entities Used:** `raw_items`, `quarantine` (inert items), normalized-content staging, `outbox` per stage.
**AI Agents Used:** PII-detection + prompt-injection-classification models (via Model Gateway, metered to `agent_runs`) — screening models, not one of the eleven named agents.
**External Integrations Used:** None (consumes F-08 connector output).
**Dependencies:** F-08, F-04.

---

## F-10 · Signal Extraction & Enrichment (the "crown jewel")

**Purpose:** Turn raw normalized content into typed objects: ticket → `FeedbackAtom`s, call → `Commitment`s, Slack thread → `DecisionCandidate`s, plus `RiskSignal`s. Cheap-first cascade so ~70% of records never touch an LLM.

F-10 is an **internal, event-chained pipeline stage**, metered to `agent_runs`. It exposes no dedicated client route; its typed output is read through F-22 (feedback), F-28 (decision candidates), F-50/F-43 downstream. The Diagnostic (F-23) drives it as an async job.

**APIs Required** — none of its own; invoked internally on the ingestion event chain and observable via the run/job grammar (the extraction work is metered to `agent_runs`, visible through `GET /api/v1/runs/{id}/steps`).

**Backend Services Used:** Extraction service (cheap-first cascade), Model Gateway (LLM tier only for the ~30% that need it).
**Database Entities Used:** `feedback_atoms`, `commitments`, `decision_candidates`, `risk_signals` (each with confidence + source pointer), `agent_runs` (metering).
**AI Agents Used:** Extraction models via the Model Gateway (LLM information extraction / typed-atom extraction); the **Research agent (A2)** consumes these downstream, but extraction itself is pipeline-level enrichment.
**External Integrations Used:** None (consumes F-09 output).
**Dependencies:** F-09.

---

## F-11 · Entity Resolution & Graph Upsert

**Purpose:** Resolve extracted atoms to canonical entities (e.g. a `FeedbackAtom` → an `Account`) and upsert into the product hierarchy **with provenance + source-ACL `read_principals`** captured at write time.

F-11 is an **internal pipeline stage**; no dedicated client route. The resolved/provenanced graph it writes is read through the hierarchy (F-03), feedback (F-22), provenance (F-14), and search/Ask (F-13) endpoints.

**APIs Required** — none of its own; runs on the ingestion event chain. Its load-bearing output (provenance + `read_principals` on every node/chunk) is what makes every later read both **citable** (F-14 provenance endpoints) and **permission-safe** (the pre-fusion ACL trim on F-10/F-13/F-22 reads).

**Backend Services Used:** Entity-resolution service (record linkage), Graph upsert service (provenance + `read_principals` capture), Tenancy (RLS context).
**Database Entities Used:** `accounts`, `products`/`features`/`epics`/… (hierarchy upserts), `provenance`/link tables, `read_principals` per node/chunk.
**AI Agents Used:** Entity-resolution model via the Model Gateway (record linkage); not one of the eleven named agents.
**External Integrations Used:** None.
**Dependencies:** F-10, F-03.

---

## F-12 · Vector & Lexical Index Fan-Out (Qdrant)

**Purpose:** Index fan-out from Postgres to Qdrant. Vectors are **derived and always rebuildable** — nothing stored in Qdrant that can't be regenerated. `document_chunks` is the Postgres↔Qdrant contract (chunk `id` *is* the Qdrant point id; `content_hash` prevents redundant re-embedding; nightly reconciler heals drift). Payload-partitioned, `organization_id` force-injected; native sparse+dense hybrid; int8 quantization; dimension-fixed collections.

F-12 is an **internal stage; Qdrant is reachable only from the retrieval service.** No client touches Qdrant. Its output is consumed by Search (F-13/F-33 → `GET /api/v1/search`) and Ask (F-29). Embedding goes through the Model Gateway (F-07).

**APIs Required** — none of its own; the index it builds is queried exclusively through `GET /api/v1/search` (§F-13) and the Ask retrieval path (§F-29), with `organization_id` force-injected into the Qdrant payload filter from TenantContext.

**Backend Services Used:** Index fan-out worker, Embedding client (Model Gateway), Qdrant interface (retrieval service only), Nightly reconciler (Postgres↔Qdrant drift heal).
**Database Entities Used:** `document_chunks` (the contract; `id` = Qdrant point id, `content_hash`), Qdrant collections (payload-partitioned, `is_tenant`-indexed `organization_id`).
**AI Agents Used:** None (embedding is a Model-Gateway call, not an agent).
**External Integrations Used:** **OpenAI** `text-embedding-3-large` @ 3072d (via Model Gateway); Qdrant is internal infra, not an external integration.
**Dependencies:** F-11, F-07 (embeddings), Qdrant provisioned.

---

## F-13 · Hybrid GraphRAG Retrieval v1

**Purpose:** The Retrieval plane: parallel vector + lexical + typed-graph traversal + governed metric-store tool calls + ledger lookups → fusion → rerank → **ACL-trimmed select (pre-fusion)** → claim-grounded generation → groundedness verification. Tiered search (coarse pre-filter → rescore → cross-encoder → LLM-select). **Numbers are tools, not text.** Honest abstention.

**APIs Required**

1. **Line "Go" — sub-50ms navigation/retrieval (no LLM in the hot path)**
   - *Endpoint:* `GET /api/v1/search`
   - *Method:* GET
   - *Request Schema:* `?q=<1..512>&kind_in=product,feature,epic,story,document,decision&limit=20&stream_id?` (`organization_id` never a param)
   - *Response Schema:* `{data:[{object_type, id, title, altitude_hint, score, uri}], withheld:{count, reason:"permissions"}, page:{...}, latency_ms}`
   - *Auth Requirements:* any member; **pre-fusion source-ACL trim** mandatory; withheld counts surfaced. Budget Go <50ms (CI-enforced).

2. **Ask retrieval (claim-grounded generation — see F-29 for the streaming surface)**
   - *Endpoint:* powers `POST /api/v1/ask` + `GET /api/v1/ask/{id}/stream` (owned by F-29) and the metric-store tool call `POST /api/v1/metrics/query` (owned by F-15)
   - *Method:* —
   - *Request Schema:* —
   - *Response Schema:* `Claim[]` with groundedness verification; any quantitative claim originates from a governed metric-store call
   - *Auth Requirements:* inherited from F-29; ACL-trimmed pre-fusion.

**Backend Services Used:** Retrieval service (the sole path to Qdrant), Hybrid-fusion + reranker (per-tenant heads), Cross-encoder, LLM-select (Model Gateway), Groundedness verifier, ACL-trim engine, Metric-store client (F-15), Ledger lookup (F-28).
**Database Entities Used:** `document_chunks`/Qdrant, `provenance`/link tables, hierarchy tables, `decision_ledger`, metric store, `read_principals`.
**AI Agents Used:** None as a named agent — retrieval is a *substrate* every generative agent (Research A2, PRD A5, Story A6, Prioritization A4, Roadmap A3, Conductor S1) calls.
**External Integrations Used:** **Anthropic Claude** (rerank/select/generation via Model Gateway).
**Dependencies:** F-12, F-11, F-07, F-15 (metric store for numeric tool calls).

---

## F-14 · Claim[] Protocol & Provenance Substrate

**Purpose:** Every AI-generated prose field is a `Claim[]` at the wire level — the protocol, not a convention. Uncitable claims marked `inference`; provenance resolvable in <400ms; redundant non-color encoding for accessibility.

**APIs Required**

1. **Resolve a citation to its evidence (the Provenance Lens — <400ms)**
   - *Endpoint:* `GET /api/v1/provenance/{id}`
   - *Method:* GET
   - *Request Schema:* none (`{id}` is a resolvable provenance handle)
   - *Response Schema:* `{source_id, source_type:"ticket"|"call"|"slack"|"document"|"metric"|"decision", uri, excerpt?, evidence_weight, captured_at, read_principals_satisfied}`; metric-backed claims return the governed query + inputs (inspectable join logic)
   - *Auth Requirements:* any member, but **resolved evidence is itself ACL-trimmed** — a withheld marker is returned instead of content the user can't see. Budget <400ms (CI-enforced).

2. **Resolve the full evidence chain for a claim**
   - *Endpoint:* `GET /api/v1/provenance/{id}/chain`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* ordered evidence chain (each link with `evidence_weight`)
   - *Auth Requirements:* same as #1 (ACL-trimmed per link).

`Claim[]` itself is enforced as the response type on **every** AI-prose field across the API (carried by F-05's wire contract); F-14's dedicated endpoints are the provenance resolvers above.

**Backend Services Used:** Provenance resolver, ACL-trim engine, Metric-store client (for metric-backed citations).
**Database Entities Used:** `provenance`/`citations` tables, `document_chunks` (chunk_id = Qdrant point id, no mapping table), metric store, `read_principals`.
**AI Agents Used:** None directly (every generative agent *produces* `Claim[]`; F-14 resolves them).
**External Integrations Used:** None.
**Dependencies:** F-13 (generation produces Claims), F-05 (`Claim[]` is a wire invariant).

---

## F-15 · Governed Metric Store

**Purpose:** A governed store of quantitative facts that AI features call as tools — the only legitimate origin for any number in any generated claim ("numbers are tools, not text"). Inspectable join logic for CFO credibility.

**APIs Required**

1. **Governed metric query (the same path agents use as a tool)**
   - *Endpoint:* `POST /api/v1/metrics/query`
   - *Method:* POST
   - *Request Schema:* `{metric_id, dimensions?, window:{from,to}}` + `Idempotency-Key` (read-like, cacheable by `(metric_id, dims, window)`)
   - *Response Schema:* `{metric_id, value, unit, join_logic:{...}, uncertainty:{low,high}, as_of, governed:true}`
   - *Auth Requirements:* any member, ACL-trimmed by metric sensitivity; metric tool calls metered (cost + provenance). `422 metric_unavailable` → claim marked "unmeasurable as predicted," never fabricated.

2. **Metric catalog (discoverable governed metrics)**
   - *Endpoint:* `GET /api/v1/metrics/catalog`
   - *Method:* GET
   - *Request Schema:* none (cursor-paginated)
   - *Response Schema:* `[{metric_id, unit, dimensions, sensitivity, description}]`
   - *Auth Requirements:* any member; sensitivity-trimmed.

**Backend Services Used:** Metric-store service (governed query engine, join-logic exposure, uncertainty bands), ACL-trim by sensitivity.
**Database Entities Used:** metric store / metric definitions, governed-query audit, freshness markers.
**AI Agents Used:** None as a named agent — every agent that emits a number (Prioritization A4, Analytics A7, Research A2, Roadmap A3, Strategist A1) calls this as a **tool**.
**External Integrations Used:** None (analytics instrumentation engines are out of scope / delegated; PMOS governs the numbers, not the instrumentation).
**Dependencies:** F-03.

---

## F-16 · Memory Plane (Four Cognitive Types × Three Scopes)

**Purpose:** Four cognitive memory types — working (task workspace), episodic (Decision Ledger + run logs, permanent), semantic (graph/indexes), procedural (templates, scoring models, anti-patterns, human-governed) — across organizational / product / user scopes. Each agent carries a memory lens.

**APIs Required**

1. **Procedural-memory governance (the human-governed slice — templates, scoring models, anti-patterns)**
   - *Endpoint:* `GET/PATCH /api/v1/admin/procedural-memory/{templates|scoring_models|anti_patterns}[/{id}]`
   - *Method:* GET, PATCH
   - *Request Schema:* `PATCH {content:{...}, change_note}` (versioned)
   - *Response Schema:* versioned procedural-memory resource (revertible)
   - *Auth Requirements:* `admin` (Olivia); versioned + revertible.

The other three memory types are **not** standalone client endpoints: *working* memory lives in the Task Workspace (read via `GET /api/v1/runs/{id}/steps`, F-06); *episodic* memory is the Decision Ledger + run logs (F-28 / F-06 endpoints); *semantic* memory is the graph/indexes (F-13 search/Ask). F-16's dedicated surface is procedural-memory governance.

**Backend Services Used:** Memory-plane service (lens resolution per agent/scope), Procedural-memory store (versioned), Working-memory/Task Workspace, links into episodic (ledger) and semantic (index) stores.
**Database Entities Used:** procedural-memory tables (`templates`, `scoring_models`, `anti_patterns`, versioned), Task Workspace, `agent_runs`/`agent_run_steps` (episodic), graph/index (semantic) — scoped org/product/user.
**AI Agents Used:** All eleven agents read their memory lens at runtime; the **Archivist (S3)** is the V2 steward (consolidation/provenance repair) — Year-1 surface is human-governed procedural memory.
**External Integrations Used:** None.
**Dependencies:** F-03, F-12, F-06.

---

## F-17 · Agent Runtime (Stateless, Checkpointed, Replayable Runs)

**Purpose:** The orchestration substrate — a NestJS orchestrator + BullMQ executing stateless, checkpointed, replayable, capability-gated runs. All state lives in the Task Workspace + memory plane; runs kept stateless/checkpointed so the eventual Temporal swap is mechanical.

**APIs Required**

1. **Universal run dispatch (the single entry point for all eleven agents)**
   - *Endpoint:* `POST /api/v1/runs`
   - *Method:* POST → `202` + job/run
   - *Request Schema:* `{agent, task_class, inputs:{decision_id?,prd_id?,candidate_set?,prompt?}, autonomy_target:"L1"|"L2", stream_id?}` + `Idempotency-Key`
   - *Response Schema:* `202 {id(=agent_runs.id), agent, task_class, status, autonomy_level, progress, parent_run_id?, child_run_ids?, token_cost, ai_generated:true}`
   - *Auth Requirements:* `editor`+ for generative/action tasks; viewers may dispatch only read-only research tasks scoped to what they can see. Year-1 rejects `autonomy_target` above L2 (`422`). Keyed dispatch → replay returns the existing run (token-cost integrity).

2. **Observe a run (SSE progress + step trace)**
   - *Endpoint:* `GET /api/v1/runs/{id}/stream` · `GET /api/v1/runs/{id}/steps` · `GET /api/v1/runs/{id}`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* SSE `step_started|step_completed|progress|run_completed|run_failed`; steps → ordered trace
   - *Auth Requirements:* run owner/viewer; SSE via fetch-header or a `run_progress` stream ticket.

3. **Cancel / replay a run**
   - *Endpoint:* `POST /api/v1/runs/{id}/cancel` · `POST /api/v1/runs/{id}/replay`
   - *Method:* POST
   - *Request Schema:* `cancel` none + key (idempotent); `replay` reconstructs from checkpoints
   - *Response Schema:* updated run; replay → reconstructed run for audit
   - *Auth Requirements:* cancel → dispatcher/`editor`+; **replay → `admin`/`owner`** (audit-dispute reconstruction).

**Backend Services Used:** Agent runtime (NestJS orchestrator + BullMQ), Checkpoint store, Task Workspace, Run-metering hook, SSE progress fan-out (via F-04 outbox).
**Database Entities Used:** `agent_runs`, `agent_run_steps`, Task Workspace, run checkpoints, `outbox`.
**AI Agents Used:** All eleven agents execute through this substrate — Conductor (S1), Sentinel (S2), Archivist (S3), Strategist (A1), Research (A2), Roadmap (A3), Prioritization (A4), PRD (A5), Story (A6), Analytics (A7), Release (A8).
**External Integrations Used:** None directly (agents reach external systems only through the governed tool service F-19).
**Dependencies:** F-06, F-07, F-04.

---

## F-18 · Policy Engine v1 & Capability Tokens (L0–L2)

**Purpose:** The autonomy contract enforced in the runtime. Authority is a consumable, audited capability token bound to `(run, task-class, approval-event, 5-min TTL)`, issued by the policy engine, verified cryptographically at the tool service. Year-1 ships L0–L2; L2 artifacts require human approval endpoints; agent writes carry `ai_generated=true` + `source_run_id`.

**APIs Required**

1. **Approval = the L2 human gate that issues the capability token**
   - *Endpoint:* `POST /api/v1/approvals/{runId}`
   - *Method:* POST
   - *Request Schema:* `{decision:"approve"|"reject", edits?, note?}` + `Idempotency-Key`
   - *Response Schema:* on approve → records the approval event (hash-chained, autonomy log) and **issues the capability token** (run/task-class/approval-event, 5-min TTL) that lets the agent's *next* tool call execute
   - *Auth Requirements:* only a human with authority over the affected object; `editor`+ (PRD/Story dispatch+approve), `admin` where the object demands. Keyed + **bound into the hash-chain** so a replayed approval cannot double-issue a token or double-append the autonomy log.

The token issuance/verification itself is internal (policy engine → governed tool service); `POST /api/v1/approvals/{runId}` is its single client-facing trigger. `403 capability_denied` is returned wherever an agent attempts a tool with no token; `409 approval_required` where an action is queued pending approval.

**Backend Services Used:** Policy engine (token issuance, autonomy matrix), Approval service, Autonomy-log writer (hash-chained, F-21), Governed tool service (token verification, F-19).
**Database Entities Used:** capability-token records (run-scoped, TTL), `autonomy_log` (append-only, hash-chained), `agent_runs` (`ai_generated`, `source_run_id`), approval events.
**AI Agents Used:** Governs all action-taking agents (PRD A5, Story A6, Roadmap A3, Prioritization A4, Release A8, Conductor S1); read-only specialists (Research A2) run L1 without a token.
**External Integrations Used:** None directly (it *gates* the external writes F-19 executes).
**Dependencies:** F-17, F-02.

---

## F-19 · Governed Tool Service

**Purpose:** The service that executes agent tool calls, verifying capability tokens cryptographically and **rejecting evidence-sourced arguments for sensitive parameters** (injection defense). Tool schemas are the contract; CI tool-call attack-success target = 0.

F-19 is an **internal service; no client calls it directly.** It surfaces through the features whose actions it executes — chiefly the metric-store tool call (F-15 `POST /api/v1/metrics/query`) and every external write (F-31 `POST /api/v1/sync/push`). It is the enforcement point named in those endpoints' auth rules.

**APIs Required** — none of its own; it is invoked by the agent runtime under a capability token and enforces the auth of `POST /api/v1/sync/push` (F-31), `POST /api/v1/metrics/query` (F-15), and any future external-write tool. `403 capability_denied` originates here when no valid token is presented.

**Backend Services Used:** Governed tool service (token verification, sensitive-arg rejection, tool-schema enforcement), Policy engine (F-18), Secret store (for external-system credentials).
**Database Entities Used:** tool-call audit (step trace in `agent_run_steps`), capability-token records, tool-schema registry.
**AI Agents Used:** Executes tool calls on behalf of action-taking agents (Story A6, PRD A5, Roadmap A3, Release A8, Conductor S1).
**External Integrations Used:** **Jira / Linear / ADO** (external writes via Living Sync, under token) · governed metric store (internal). Reads/writes back to source systems only here, only under a capability token.
**Dependencies:** F-18, F-17.

---

## F-20 · Eval Harness (Release-Gating)

**Purpose:** The evaluation harness that gates releases — "built second, not last." Measures normalized edit-distance on accepted stories, time-to-approval per (team, task-type), groundedness, and the honesty/abstention metric (≥95%). Encodes the kill/pivot trigger (>30% edit-distance after two quarters).

F-20 is **release-gating CI machinery + a quality dashboard, not a request-time client API.** It consumes edit-distance signals captured on artifact `PATCH` (F-03/F-24/F-25) and run/step data (F-06), and it *blocks* drafts from surfacing rather than serving an endpoint. Its read surface is the quality dashboard (a GraphQL usage/quality lens via `POST /api/graphql`).

**APIs Required**

1. **Quality / edit-distance dashboard (GraphQL lens)**
   - *Endpoint:* `POST /api/graphql` (quality/edit-distance lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash (quality lens) + variables `{team_id?, task_class?, window?}`
   - *Response Schema:* per-(team, task-class) edit-distance, approval latency, groundedness, abstention metrics; kill/pivot-trigger state
   - *Auth Requirements:* `admin`/`editor` (engineering-lead/Priya visibility).

Eval-gate failures surface as **blocked drafts** on the generative features (`eval-gate failure → draft never surfaced` on F-24/F-25), not as a standalone request error.

**Backend Services Used:** Eval harness (CI release gate), Edit-distance computer, Groundedness/abstention scorer, Quality-dashboard projection.
**Database Entities Used:** edit-distance records (per accepted artifact), approval-latency logs, groundedness/abstention eval results, gold-standard sets.
**AI Agents Used:** Evaluates the output of all generative agents (PRD A5, Story A6, Research A2); not an agent itself.
**External Integrations Used:** None.
**Dependencies:** F-17, F-13 (must precede broad agent rollout).

---

## F-21 · Audit Fabric (Append-Only Hash-Chained Ledger Substrate)

**Purpose:** One audit pattern for all auditable surfaces — append-only Postgres tables, each row `row_hash = H(prev_hash ‖ canonical_payload)`, hourly chain-head anchors to WORM S3, full OTel trace linkage. `UPDATE`/`DELETE` revoked at the role level. Underpins the Decision Ledger, autonomy log, and billing meters.

**APIs Required**

1. **Verify the hash chain (re-walk + verified head)**
   - *Endpoint:* `GET /api/v1/audit/verify`
   - *Method:* GET
   - *Request Schema:* optional `?chain=decision|autonomy|billing`
   - *Response Schema:* `{chain, verified_head:{row_hash, anchored_at}, integrity:"ok"|"mismatch"}`
   - *Auth Requirements:* `admin`/`owner` (security-review / M&A-diligence surface); chain-head mismatch → integrity alert (sev-1).

F-21 is a **substrate**: the append + hash-chain + WORM-anchor behavior is invoked inside `POST /api/v1/decisions/commit` (F-28/F-30), `POST /api/v1/approvals/{runId}` (F-18), and billing-meter writes (F-34). Its only dedicated client route is chain verification above.

**Backend Services Used:** Audit-fabric service (hash-chain append, canonical payload hashing), WORM anchor worker (hourly → S3), OTel trace linkage, chain-verify walker.
**Database Entities Used:** append-only hash-chained tables (`decision_ledger`, `autonomy_log`, `billing_meters`), WORM S3 anchor records, `row_hash`/`prev_hash` columns.
**AI Agents Used:** None (platform).
**External Integrations Used:** **AWS S3 (WORM)** for hourly chain-head anchoring.
**Dependencies:** F-03, S3/WORM storage.

---

# 2. MVP (F-22 … F-34)

*The Year-1 wedge and the minimum closed loop that proves 30-day ROI — "The Product Org's Memory" (25–40 mid-market logos).*

---

## F-22 · Feedback Intelligence (the wedge)

**Purpose:** Every signal clustered, quantified (account/revenue via the governed metric store), tied to accounts, threaded to the decisions it should inform. The primary value-proof surface; must prove ≥5–6 hrs/PM/week recovered within 30 days on tickets+docs alone.

**APIs Required**

1. **Feedback-cluster lens (compose clusters + quantification + provenance)**
   - *Endpoint:* `POST /api/graphql` (feedback-cluster lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash + variables `{topic?, stream_id?, account_id?}`; quantification via governed metric-store calls
   - *Response Schema:* clusters `{quantification:Claim[] (e.g. "Billing friction 3× on enterprise, $1.2M ARR" — every number metric-store-cited), member_atoms:[{provenance, confidence}], low_signal?:flagged inference}`
   - *Auth Requirements:* any member, **ACL-trimmed atoms** ("n withheld"); complexity-budgeted.

2. **Read a single cluster**
   - *Endpoint:* `GET /api/v1/feedback/clusters/{id}`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* cluster with member atoms, provenance, quantification
   - *Auth Requirements:* any member, ACL-trimmed.

3. **Thread a cluster to a decision/artifact (leads into the commit ceremony)**
   - *Endpoint:* `POST /api/v1/feedback/clusters/{id}/thread`
   - *Method:* POST
   - *Request Schema:* `{target:{type:"decision"|"prd", id}}` + `Idempotency-Key` (threading the same cluster to the same target is idempotent)
   - *Response Schema:* thread link resource
   - *Auth Requirements:* `editor`+.

4. **Research-agent synthesis (the agent face of the wedge)**
   - *Endpoint:* `POST /api/v1/runs` (`agent:"research"`, `task_class:"synthesize_feedback"`)
   - *Method:* POST → `202`
   - *Request Schema:* `{cluster_id?, topic?, stream_id?}` + `Idempotency-Key`
   - *Response Schema:* `202` run → cited synthesis (`Claim[]`, contrarian probe)
   - *Auth Requirements:* `editor`+ (read-only synthesis at L1; viewers scoped to what they can see).

**Backend Services Used:** Feedback Intelligence (clustering, quantification, threading), Retrieval (F-13), Metric-store client (F-15), Agent runtime (F-17, Research), GraphQL gateway.
**Database Entities Used:** `feedback_atoms`, `accounts`, `insights`, `claims`/`citations`, metric store, thread/link tables.
**AI Agents Used:** **Research (A2)** (synthesis, contrarian probe); Sentinel (S2) risk-threading is V2.
**External Integrations Used:** None directly (consumes ingested signal); revenue/account numbers governed only.
**Dependencies:** F-10, F-11, F-13, F-15.

---

## F-23 · The Free Diagnostic (GTM)

**Purpose:** A free, self-serve diagnostic run as the GTM wedge — ingests a prospect's accessible sources and surfaces findings to prove value before purchase. Built on the async-job grammar.

**APIs Required**

1. **Launch the Diagnostic (async job under a trial principal)**
   - *Endpoint:* `POST /api/v1/diagnostic`
   - *Method:* POST → `202`
   - *Request Schema:* `{connector_refs:[uuid], scope:"read_only"}` + `Idempotency-Key`
   - *Response Schema:* `202 {job:{id, status, progress}}`
   - *Auth Requirements:* **scoped, time-boxed trial principal** established at marketing-led consent (the one near-public surface); read-only connector scopes only; trial data isolated and purged per trial policy.

2. **Read / stream Diagnostic progress + result**
   - *Endpoint:* `GET /api/v1/diagnostic/{id}` · `GET /api/v1/diagnostic/{id}/stream`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* a cited findings report + honest coverage estimate and gaps; insufficient data → honest "low coverage" result
   - *Auth Requirements:* the trial principal that launched it.

**Backend Services Used:** Diagnostic orchestrator (async job), Ingestion pipeline (F-08/F-09 on the bulk lane), Feedback Intelligence (F-22), Coverage estimator.
**Database Entities Used:** trial-scoped (isolated) `raw_items`, `feedback_atoms`, `insights`, `claims`, coverage estimate, `jobs`.
**AI Agents Used:** **Research (A2)** (feedback synthesis); extraction models (F-10).
**External Integrations Used:** the prospect's read-only connectors (**Zendesk/Jira/Notion/Confluence** etc., F-08).
**Dependencies:** F-22, F-08, F-09.

---

## F-24 · Artifact Engine — Evidence-Native PRD (PRD Agent, L1/L2)

**Purpose:** PRD generation where every sentence traces to sources (`Claim[]`); decision → build-ready spec. Mandatory contrarian probe ("evidence against"). L1 draft / L2 act-with-approval; `ai_generated=true` + `source_run_id`; edit-distance logged.

**APIs Required**

1. **Dispatch a PRD draft run**
   - *Endpoint:* `POST /api/v1/runs` (`agent:"prd"`, `task_class:"draft_prd"`)
   - *Method:* POST → `202`
   - *Request Schema:* `{decision_id, template_id?, stream_id?}` + `Idempotency-Key`
   - *Response Schema:* `202` run → streamed `Claim[]` PRD sections (incl. an explicit "evidence against" section)
   - *Auth Requirements:* `editor`+ with authority over the source decision.

2. **Stream the draft**
   - *Endpoint:* `GET /api/v1/runs/{id}/stream`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* SSE run progress + streamed claim sections
   - *Auth Requirements:* run owner; eval-gate failure → draft never surfaced (F-20).

3. **L2 approval (issues the capability token)**
   - *Endpoint:* `POST /api/v1/approvals/{runId}`
   - *Method:* POST
   - *Request Schema:* `{decision:"approve", edits?:{non_goals?,...}}` + `Idempotency-Key` (edits before approval recorded as edit-distance)
   - *Response Schema:* approval event recorded; on approve → commit allowed
   - *Auth Requirements:* `editor`+ with authority over the source decision (the L2 gate).

4. **Commit the approved PRD (new immutable version)**
   - *Endpoint:* `POST /api/v1/prds` · `GET /api/v1/prds/{id}`
   - *Method:* POST, GET
   - *Request Schema:* commit keyed; idempotent on `(decision_id, source_run_id, content_hash)`
   - *Response Schema:* versioned PRD document (§F-03 shape), `ai_generated:true`, `source_run_id`, edit-distance summary
   - *Auth Requirements:* `editor`+.

**Backend Services Used:** Agent runtime (F-17), Policy engine/approval (F-18), Retrieval (F-13), Metric-store client (F-15), Claim/provenance (F-14), Document/versioning (F-03), Eval harness gate (F-20).
**Database Entities Used:** `documents`/`document_versions` (PRD), `claims`/`citations`, `agent_runs`/`agent_run_steps`, edit-distance records, `decision_ledger` (source decision).
**AI Agents Used:** **PRD (A5)**; Conductor (S1) when orchestrated.
**External Integrations Used:** **Anthropic Claude** (generation via Model Gateway).
**Dependencies:** F-13, F-14, F-17, F-18, F-16, F-20.

---

## F-25 · Artifact Engine — Story Writing (Story Agent, L1/L2)

**Purpose:** Spec → engineer-grade epics/stories/ACs as evidence-native `Claim[]`. L1/L2 in Year-1 (the L3 Jira push is V2). Edit-distance and approval latency measured per (team, task-class) — the P4 advocacy instrument.

**APIs Required**

1. **Dispatch a story-writing run**
   - *Endpoint:* `POST /api/v1/runs` (`agent:"story"`, `task_class:"write_stories"`)
   - *Method:* POST → `202`
   - *Request Schema:* `{prd_id, target_team_id?}` + `Idempotency-Key`
   - *Response Schema:* `202` run → streamed epic tree + stories + ACs as `Claim[]`
   - *Auth Requirements:* `editor`+.

2. **Stream the draft**
   - *Endpoint:* `GET /api/v1/runs/{id}/stream`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* SSE run progress + streamed claims; sustained edit-distance >30% after the tuning window → **kill/pivot trigger** (scope freeze; surfaced on the quality dashboard)
   - *Auth Requirements:* run owner.

3. **L2 approval (Priya's bar)**
   - *Endpoint:* `POST /api/v1/approvals/{runId}`
   - *Method:* POST
   - *Request Schema:* `{decision:"approve", edits?}` + `Idempotency-Key`
   - *Response Schema:* approval event; engineer-grade structure validated against gold standards in the eval harness
   - *Auth Requirements:* `editor`+/`admin`.

4. **Commit the hierarchy (epics / user-stories / requirements)**
   - *Endpoint:* `POST /api/v1/epics` · `POST /api/v1/user-stories` · `POST /api/v1/requirements`
   - *Method:* POST
   - *Request Schema:* per §F-03 hierarchy schemas; story commit idempotent on `(prd_id, source_run_id, content_hash)`
   - *Response Schema:* created hierarchy rows with provenance + edit-distance records (`ai_generated`/`source_run_id`)
   - *Auth Requirements:* `editor`+. (The L3 external push to Jira/Linear/ADO is **not** exposed Year-1 — that is F-47, V2.)

**Backend Services Used:** Agent runtime (F-17), Approval/policy (F-18), Retrieval (F-13), Claim/provenance (F-14), Hierarchy/domain (F-03), Eval harness (F-20).
**Database Entities Used:** `epics`, `user_stories`, `requirements`, `claims`/`citations`, edit-distance records, `agent_runs`, `documents` (source PRD).
**AI Agents Used:** **Story Writing (A6)**; Conductor (S1) when orchestrated.
**External Integrations Used:** **Anthropic Claude** (via Model Gateway). External execution-tool write is V2.
**Dependencies:** F-24, F-13, F-20.

---

## F-26 · Conductor Agent (AI Chief of Staff)

**Purpose:** System agent S1 — intake, planning, delegation to specialists, assembly of results, submission to humans. The orchestration brain.

**APIs Required**

1. **Dispatch a Conductor run (creates parent + capability-scoped child runs)**
   - *Endpoint:* `POST /api/v1/runs` (`agent:"conductor"`)
   - *Method:* POST → `202`
   - *Request Schema:* `{task_class, inputs:{prompt?,...}, autonomy_target:"L1"|"L2", stream_id?}` + `Idempotency-Key`
   - *Response Schema:* parent run with `child_run_ids[]`; child runs inherit tenant context and carry their own narrower capability scope; assembles a review package submitted to the human (judgment only)
   - *Auth Requirements:* `editor`+; child runs governed by F-18 tokens.

2. **Observe the parent/child run tree**
   - *Endpoint:* `GET /api/v1/runs/{id}` · `GET /api/v1/runs/{id}/stream` · `GET /api/v1/runs/{id}/steps`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* run tree with `parent_run_id`/`child_run_ids`, step trace
   - *Auth Requirements:* run owner/viewer.

**Backend Services Used:** Agent runtime (F-17, parent/child orchestration), Policy engine (F-18, child-scope tokens), Memory plane (F-16), all specialist services it delegates to.
**Database Entities Used:** `agent_runs` (parent/child), `agent_run_steps`, Task Workspace, `ai_agents`.
**AI Agents Used:** **Conductor (S1)** orchestrating Research (A2), PRD (A5), Prioritization (A4), Roadmap (A3), Story (A6), etc.
**External Integrations Used:** None directly.
**Dependencies:** F-17, F-18, F-16.

---

## F-27 · Research Agent

**Purpose:** Specialist A2 — evidence work: feedback synthesis, interviews, market context. The agent face of Feedback Intelligence; runs L1 (draft) without an approval gate.

**APIs Required**

1. **Dispatch a research run**
   - *Endpoint:* `POST /api/v1/runs` (`agent:"research"`, `task_class:"synthesize_feedback"` | other read-only classes)
   - *Method:* POST → `202`
   - *Request Schema:* `{cluster_id?, topic?, stream_id?}` + `Idempotency-Key`
   - *Response Schema:* `202` run → cited synthesis (`Claim[]`, summarization with citations, contrarian probe); no L2 token needed (read-only)
   - *Auth Requirements:* `editor`+; viewers may dispatch read-only research scoped to what they can see.

2. **Stream / read the run** — `GET /api/v1/runs/{id}/stream` · `GET /api/v1/runs/{id}` (per F-17).

**Backend Services Used:** Agent runtime (F-17), Retrieval (F-13), Feedback Intelligence (F-22), Metric-store client (F-15).
**Database Entities Used:** `feedback_atoms`, `insights`, `interviews`, `claims`/`citations`, `agent_runs`.
**AI Agents Used:** **Research (A2)**.
**External Integrations Used:** **Anthropic Claude** (via Model Gateway).
**Dependencies:** F-22, F-13, F-17.

---

## F-28 · Decision Ledger v1

**Purpose:** Every decision a first-class, versioned, hash-chained object — options, evidence, assumptions, predicted impact, owner, dissent, review date ("git for product decisions"). Entries are a **byproduct of actions people already take** (no standalone "log a decision" form).

**APIs Required**

1. **Read a decision + its history**
   - *Endpoint:* `GET /api/v1/decisions/{id}` · `GET /api/v1/decisions/{id}/history`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* `{id, version, question:Claim[], the_call:Claim[], options, evidence, assumptions, predicted_impact, owner, dissent, review_date, guards, ledger:{row_hash, prev_hash, anchored, signature_verified}}`
   - *Auth Requirements:* any member, ACL-trimmed; ledger is **append-only** (`UPDATE`/`DELETE` role-revoked; a "change" appends a new version).

2. **Decision Sheet lens (compose the sheet for a decision)**
   - *Endpoint:* `POST /api/graphql` (Decision Sheet lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash + `{decision_id}`
   - *Response Schema:* composed sheet (The Question, The Call, evidence, assumptions, guards, outcome attachment) — all prose `Claim[]`
   - *Auth Requirements:* any member, ACL-trimmed.

(The *write* path — commit — is F-30; the *verify* path is F-21 `GET /api/v1/audit/verify`.)

**Backend Services Used:** Decision/ledger service, Audit fabric (F-21, hash-chain), Retrieval (F-13, evidence lookup), GraphQL gateway.
**Database Entities Used:** `decision_ledger` (append-only, hash-chained, versioned), `assumptions`, `guards`, `dissent`, `evidence`/`citations`, `decision_candidates` (Archivist-mined, V2).
**AI Agents Used:** None core (capture is a byproduct); enriched later by the **Archivist (S3)**, V2.
**External Integrations Used:** None.
**Dependencies:** F-21, F-03, F-30 (commit ceremony populates it).

---

## F-29 · Ask-the-Brain v1 (Org-Wide Product Brain)

**Purpose:** Anyone asks "why don't we support SSO on Starter?" and gets decision + evidence + owner + review date — claim-grounded, ACL-trimmed, honest abstention. Token/claim streaming over SSE; first token <700ms. Ubiquity engine (free viewers).

**APIs Required**

1. **Initiate an Ask**
   - *Endpoint:* `POST /api/v1/ask`
   - *Method:* POST
   - *Request Schema:* `{question:<1..4000>, conversation_id?, stream_id?, mode?:"ask"}` + `Idempotency-Key` (replay returns the same `conversation_id`/`stream_path`)
   - *Response Schema:* `200 {conversation_id, stream_path:"/api/v1/ask/{id}/stream"}`
   - *Auth Requirements:* **any member, including free viewers**.

2. **Stream the answer (SSE: tokens, claims, citations, abstentions)**
   - *Endpoint:* `GET /api/v1/ask/{conversationId}/stream`
   - *Method:* GET (SSE)
   - *Request Schema:* fetch-header auth or a 60s `ask` stream ticket
   - *Response Schema:* SSE `token | claim | citation | abstention | done | error`; each claim renders a Provenance Underline; `kind:"simulated"` ⇒ violet; abstention `{reason:"withheld_by_permissions"|"ungroundable", withheld_count}`
   - *Auth Requirements:* asker; retrieval applies **pre-fusion source-ACL trim**; "n sources withheld by permissions" surfaced honestly. Budget first token <700ms.

3. **Read conversation history** — `GET /api/v1/conversations/{id}` (per F-06).
4. **Resolve provenance** — `GET /api/v1/provenance/{source_id}` (per F-14, <400ms).

**Backend Services Used:** Ask/QA service, Retrieval (F-13), Claim/provenance (F-14), Metric-store client (F-15, numbers as tools), Decision/ledger lookup (F-28), Conversation service (F-06), SSE/stream-ticket (F-02/F-05).
**Database Entities Used:** `conversations`, `messages`, `claims`/`citations`, `decision_ledger`, `document_chunks`/Qdrant, metric store, `read_principals`.
**AI Agents Used:** None as a named agent (claim-grounded QA over the retrieval substrate); a quantitative claim must carry a metric-store citation or be marked unmeasurable.
**External Integrations Used:** **Anthropic Claude** (generation via Model Gateway, with failover quality badge).
**Dependencies:** F-13, F-14, F-28.

---

## F-30 · Commit Ceremony & Decision Sheet

**Purpose:** The ceremonial write surface where a human commits a decision (typed initial → hash-chained ledger entry). The Decision Sheet presents The Question and The Call; supports a Pre-Mortem and guards. Interactive write path: BFF → domain service → Postgres tx (state + outbox) + hash-chain append + signature verification.

**APIs Required**

1. **Commit a decision (the ceremony — strongest idempotency in the system)**
   - *Endpoint:* `POST /api/v1/decisions/commit`
   - *Method:* POST
   - *Request Schema:* `{context:{source_action:"prd_approval"|"arena_ranking"|"roadmap_resequence", source_run_id?, prd_id?, ranking_id?, roadmap_id?}, question:Claim[], the_call:Claim[], options?, evidence_refs:[source_id], assumptions:[{id,text:Claim[]}], predicted_impact:{metric_id,value,window}, owner_id, dissent?, review_date, signature:{typed_initial, ts}}` + `Idempotency-Key` (**bound into the hash-chain**)
   - *Response Schema:* decision resource with `ledger:{row_hash, prev_hash, anchored, signature_verified}`
   - *Auth Requirements:* `editor`+ with authority over the affected objects; signature verified server-side before the hash-chain append; replay returns the already-appended entry (cannot double-append).

2. **Run a Pre-Mortem on a decision**
   - *Endpoint:* `POST /api/v1/decisions/{id}/premortem`
   - *Method:* POST → `202`
   - *Request Schema:* `{}` + `Idempotency-Key`
   - *Response Schema:* `202` run (V2 strategist powers synthetic stakeholders; Year-1 returns a structured prompt scaffold)
   - *Auth Requirements:* `editor`+.

3. **Add a guard (e.g. "gate at 5% until A3 verifies")**
   - *Endpoint:* `POST /api/v1/decisions/{id}/guards`
   - *Method:* POST
   - *Request Schema:* `{guard, assumption_id, threshold}` + `Idempotency-Key`
   - *Response Schema:* updated decision with the guard appended
   - *Auth Requirements:* `editor`+.

**Backend Services Used:** Decision/ledger service, Audit fabric (F-21, signature verify + hash-chain append + WORM anchor), Outbox (same-tx event), Agent runtime (F-17, premortem run), Metric-store client (F-15, predicted-impact governance).
**Database Entities Used:** `decision_ledger` (append), `assumptions`, `guards`, `dissent`, signature payload, `outbox`, WORM anchor.
**AI Agents Used:** None core; Pre-Mortem uses synthetic stakeholders (**Strategist A1**, V2).
**External Integrations Used:** None.
**Dependencies:** F-28, F-21, F-05.

---

## F-31 · Living Sync v1 (One-Way Spec → Jira/Linear/ADO)

**Purpose:** One-way sync pushing the spec layer to execution tools with diffs + rationale; the foundation for bidirectional drift detection. PMOS syncs, never replaces, the execution tool.

**APIs Required**

1. **Push (preview diff, then execute under L2 approval + capability token)**
   - *Endpoint:* `POST /api/v1/sync/push`
   - *Method:* POST
   - *Request Schema:* `{object_ref:{type:"epic"|"story", id}, target:"jira"|"linear"|"ado", dry_run?:true}` + `Idempotency-Key` (idempotent on `(object_ref, target, content_hash)`)
   - *Response Schema:* `{id, diff:[...], rationale:Claim[], external_ids?, status:"previewed"|"pushed"|"failed", revert_handle? (V2)}`
   - *Auth Requirements:* `editor`+ and an **L2 approval before any external write**; the write executes **only** through the governed tool service (F-19) under a capability token; evidence-sourced args rejected for sensitive params. External half-success → **compensating action surfaced for a human** (no auto saga rollback Year-1).

2. **Read sync status / state**
   - *Endpoint:* `GET /api/v1/sync/{id}` · `GET /api/v1/sync/state?object_ref=`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* sync record / per-object sync state (drift surfaced)
   - *Auth Requirements:* `editor`+/viewer of the object.

**Backend Services Used:** Living Sync service (diff + rationale), Governed tool service (F-19, external write under token), Policy engine/approval (F-18), Connector SDK (F-08, target adapters).
**Database Entities Used:** `sync_records`, `external_id_map`, `diffs`, `claims` (rationale), capability-token records.
**AI Agents Used:** Diff/rationale generation (via Model Gateway); **Story (A6)**/**PRD (A5)** produce the artifacts synced. Bidirectional + Story L3 is V2 (Roadmap/Release).
**External Integrations Used:** **Jira / Linear / ADO** (one-way external write, under capability token).
**Dependencies:** F-08, F-19, F-24/F-25 (produce the artifacts to sync).

---

## F-32 · The Brief / Standing Brief v1 + Notify

**Purpose:** Continuously-current narrative re-rendered from the ledger (never stored stale), published by local 6am ≥99.5% of days. "The system speaks first." Every claim provenance-linked. Plus baseline notify.

**APIs Required**

1. **Brief lens (compose ranked findings + recommended actions + freshness)**
   - *Endpoint:* `POST /api/graphql` (Brief lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash + `{stream_id?, altitude?}`
   - *Response Schema:* ranked findings (`Claim[]`), recommended actions inline, **freshness badge**; per-reader (localized, ACL-trimmed) from the one graph; re-rendered from projections, never stored stale
   - *Auth Requirements:* any member; each user sees only findings their ACL permits. Budget cold load <1.5s; published by local 6am ≥99.5%.

2. **Notification preferences**
   - *Endpoint:* `GET /api/v1/notifications/preferences` · `PATCH /api/v1/notifications/preferences`
   - *Method:* GET, PATCH
   - *Request Schema:* `PATCH {interrupt_only_vermilion?, digest_cadence?:"daily"|"off", channels?:["in_app","email"]}`
   - *Response Schema:* preferences resource
   - *Auth Requirements:* self.

**Backend Services Used:** Brief renderer (ledger → narrative projection), Retrieval (F-13), Claim/provenance (F-14), Projection builder (F-04 outbox-driven), Notify service.
**Database Entities Used:** `decision_ledger`, projections (Brief), `claims`/`citations`, notification preferences, freshness markers.
**AI Agents Used:** Claim-grounded narrative generation + finding ranking (via Model Gateway); Sentinel (S2) feeds findings in V2.
**External Integrations Used:** **Anthropic Claude** (narrative via Model Gateway); email channel for digests (notify).
**Dependencies:** F-28, F-14, F-13, F-04 (projections).

---

## F-33 · The Line (Command Interface) & Search v1

**Purpose:** The single command interface (`⌘K`, three blended modes — Go / Ask / Do). Go <50ms; Ask streams claims; Do triggers agent actions. The IA omits folders/page-trees/global lists — to "organize," ask the Line.

**APIs Required**

1. **Go — instant navigation/retrieval**
   - *Endpoint:* `GET /api/v1/search` (per F-13)
   - *Method:* GET
   - *Request Schema:* `?q&kind_in&limit&stream_id?`
   - *Response Schema:* ranked objects with `altitude_hint`, withheld counts, `latency_ms`
   - *Auth Requirements:* any member, ACL-trimmed; Go <50ms.

2. **Ask — claim-streaming Q&A**
   - *Endpoint:* `POST /api/v1/ask` + `GET /api/v1/ask/{id}/stream` (per F-29)
   - *Method:* POST, GET (SSE)
   - *Request Schema:* `{question, ...}`
   - *Response Schema:* streamed `Claim[]`
   - *Auth Requirements:* any member (viewers included).

3. **Do — trigger an agent action**
   - *Endpoint:* `POST /api/v1/runs` (per F-17; agent/task_class from intent classification)
   - *Method:* POST → `202`
   - *Request Schema:* `{agent, task_class, inputs, autonomy_target}` derived from the parsed "Do" intent
   - *Response Schema:* `202` run
   - *Auth Requirements:* `editor`+ for action tasks.

**Backend Services Used:** Line/command service (Go/Ask/Do intent classification), Retrieval (F-13), Ask/QA (F-29), Agent runtime (F-17).
**Database Entities Used:** search index/Qdrant, hierarchy tables, `conversations`, `agent_runs`.
**AI Agents Used:** Intent classification (Go/Ask/Do routing, via Model Gateway); routes "Do" into any of the eleven agents.
**External Integrations Used:** **Anthropic Claude** (intent/Ask via Model Gateway).
**Dependencies:** F-13 (Ask/Go), F-29, F-05.

---

## F-34 · Consumption Metering & Billing Meters

**Purpose:** Per-autonomy-unit metering built on `agent_runs` token accounting; append-only billing meters via the audit fabric. Exposes a predictable platform fee and metered autonomy units side by side.

**APIs Required**

1. **Read billing meters (append-only, hash-chained)**
   - *Endpoint:* `GET /api/v1/billing/meters`
   - *Method:* GET
   - *Request Schema:* `?from&to&group_by=agent|task_type`
   - *Response Schema:* per-agent/per-task-type consumption mapped to cost (append-only; no edit)
   - *Auth Requirements:* `owner`/finance.

2. **Usage lens (GraphQL)**
   - *Endpoint:* `POST /api/graphql` (usage lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash + `{window, group_by?}`
   - *Response Schema:* composed usage/consumption view
   - *Auth Requirements:* `owner`/finance/`admin`.

**Backend Services Used:** Metering service (reads `agent_runs` token meters), Billing-meter writer (F-21 append-only hash-chain), GraphQL gateway.
**Database Entities Used:** `agent_runs` (token cost), `billing_meters` (append-only, hash-chained), plan config.
**AI Agents Used:** None (reads token meters; every metered agent run contributes).
**External Integrations Used:** None (billing/invoicing system integration not specified Year-1; meters are the source).
**Dependencies:** F-06, F-21.

---

# 3. V1 (F-35 … F-42)

*Remainder of Year-1 GA — the canvas, altitudes, and design-system surfaces that make the loop usable end-to-end, plus the early specialist agents and the compliance/resilience posture that gates deals.*

---

## F-35 · The Meridian Canvas & Altitudes

**Purpose:** One canvas, many lenses. The Meridian: one horizontal spatial axis (left = evidence/past, right = plans/future; outcomes flow right→left). Three altitudes — Org (30k ft) · Stream (3k ft) · Object (ground). Canvas pan/zoom ≥60fps.

The canvas is a **front-end rendering of graph state**; it issues no canvas-specific mutations. It composes its views from GraphQL lenses and renders them client-side (no per-frame API call).

**APIs Required**

1. **Stream Canvas lens (compose the canvas at the chosen altitude)**
   - *Endpoint:* `POST /api/graphql` (Stream Canvas lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash + `{stream_id?, altitude:"org"|"stream"|"object", viewport?}`; complexity-budgeted (cost ≤1000, depth ≤8)
   - *Response Schema:* the graph slice for the altitude (objects, positions on the Meridian, provenance underlines, freshness) — AI prose as `Claim[]`
   - *Auth Requirements:* any member, ACL-trimmed; payload size bounded by the complexity budget to hold ≥60fps client-side.

2. **Object reads as the user drills to ground** — the hierarchy/document/decision GET endpoints (F-03, F-28) and `GET /api/v1/provenance/{id}` (F-14) supply object-altitude detail.

**Backend Services Used:** GraphQL gateway (lens composition, DataLoader batching, complexity budget), Retrieval (F-13), Claim/provenance (F-14), Hierarchy/domain (F-03).
**Database Entities Used:** hierarchy tables, `decision_ledger`, `insights`, `claims`/`citations`, projections.
**AI Agents Used:** None directly (renders graph state).
**External Integrations Used:** None.
**Dependencies:** F-05 (GraphQL lenses), F-03.

---

## F-36 · Streams, Lenses & Brief Containers

**Purpose:** The container model — **Stream** (the only human-curated container, a durable area of responsibility), **Lens** (a saved, shareable canvas configuration), **Brief** (generated ephemeral narrative). Workspace = one company = one graph.

**APIs Required**

1. **Stream CRUD + membership**
   - *Endpoint:* `POST /api/v1/streams` · `GET /api/v1/streams/{id}` · `PATCH /api/v1/streams/{id}` · `DELETE /api/v1/streams/{id}` · `POST /api/v1/streams/{id}/members` · `DELETE /api/v1/streams/{id}/members/{userId}`
   - *Method:* POST, GET, PATCH, DELETE
   - *Request Schema:* `POST /streams {name, description?, object_refs?:[{context_type,context_id}]}`; `POST .../members {user_id, stream_role:"owner"|"contributor"|"viewer"}` — each + `Idempotency-Key`
   - *Response Schema:* Stream resource `{id, name, owner, membership, created_at, updated_at}`
   - *Auth Requirements:* create/curate → `editor`+ (a PM owns their Streams); member add/remove → Stream owner or `admin`; delete is soft-delete; member add idempotent.

2. **Lens CRUD + share (share never widens ACL)**
   - *Endpoint:* `POST /api/v1/lenses` · `GET /api/v1/lenses/{id}` · `POST /api/v1/lenses/{id}/share`
   - *Method:* POST, GET
   - *Request Schema:* `POST /lenses {name, canvas_config:{...}, stream_id?}`; `POST .../share {audience:"stream"|"workspace"|"users", user_ids?}` — each + `Idempotency-Key`
   - *Response Schema:* Lens resource with `canvas_config`, `shared_with`
   - *Auth Requirements:* Lens owner; **share never widens ACL** — a shared Lens renders trimmed per viewer's own source-ACL.

3. **Workspace membership / role changes**
   - *Endpoint:* `GET /api/v1/workspaces/{wsId}/members` · `PATCH /api/v1/workspaces/{wsId}/members/{userId}`
   - *Method:* GET, PATCH
   - *Request Schema:* `PATCH {role:"viewer"|"editor"|"admin"|"owner"}`
   - *Response Schema:* membership resource
   - *Auth Requirements:* workspace member role changes → `admin`/`owner` only (no self-demotion of the last `owner`).

4. **Brief container** — the Brief is read via the Brief lens (`POST /api/graphql`, per F-32); it is ephemeral, never stored stale.

**Backend Services Used:** Stream/Lens service, Membership/ABAC service, GraphQL gateway (Brief/canvas lenses), Tenancy (RLS).
**Database Entities Used:** `streams`, `stream_members`, `lenses` (`canvas_config`, `shared_with`), `workspace_members`, polymorphic `object_refs` `(context_type, context_id)`.
**AI Agents Used:** None (platform).
**External Integrations Used:** None.
**Dependencies:** F-35, F-03.

---

## F-37 · The Tide (Ranked Notifications) & Meridian Bar

**Purpose:** The Tide — calm, ranked notifications that interrupt only for Vermilion (contradiction/risk). The Meridian Bar — bottom strip with waypoints (`⌘1–5`), time scrubber, altitude control. Delivered over SSE.

**APIs Required**

1. **Tide stream (SSE)**
   - *Endpoint:* `GET /api/v1/tide/stream`
   - *Method:* GET (SSE)
   - *Request Schema:* fetch-header auth or a `tide` stream ticket
   - *Response Schema:* SSE `tide_item | tide_clear | interrupt`; each item `{id, rank, hue:"verdant"|"amber"|"vermilion"|..., finding:Claim[], source_run_id?, uri}`; interruption gated to Vermilion server-side
   - *Auth Requirements:* each user sees only items their ACL permits; one Tide SSE per session.

2. **Recent Tide (paginated) + ack**
   - *Endpoint:* `GET /api/v1/tide` · `POST /api/v1/tide/{itemId}/ack`
   - *Method:* GET, POST
   - *Request Schema:* list cursor-paginated; `ack {}` + `Idempotency-Key` (idempotent)
   - *Response Schema:* recent items; ack → acknowledged state
   - *Auth Requirements:* item recipient.

**Backend Services Used:** Tide/notification service (ranking, Vermilion gating), SSE fan-out (F-04 outbox), Stream-ticket (F-02).
**Database Entities Used:** `tide_items` (rank, hue, finding), ack state, `claims` (findings), `source_run_id`.
**AI Agents Used:** Finding ranking/prioritization; **Sentinel (S2)** is the V2 engine that feeds risk/contradiction items (ranking can ship with MVP signals first).
**External Integrations Used:** None.
**Dependencies:** F-05 (SSE); F-43 (Sentinel) for full value — ranking ships with MVP signals first.

---

## F-38 · Meridian Design System

**Purpose:** The full design system — two atmospheres (Daylight/Midnight), five semantic hues, role-correct typography, the **Provenance Underline** (thickness + glyph encoding evidence weight, accessible), physics-based motion, WCAG 2.2 AA, per-reader i18n from one graph.

F-38 is a **front-end presentation layer** (design tokens, components, the Provenance Underline renderer). It introduces **no new API endpoints**; it consumes the `Claim[]` wire shape (to render the Provenance Underline by `evidence_weight`) and the per-reader localization fields that already flow through existing reads.

**APIs Required** — none of its own. It renders:
- `Claim[]` `citations[].evidence_weight` → Provenance Underline classes (`.prov-single`/`.prov-corroborated`/`.prov-inference`/`.prov-simulated`/`.prov-degraded`) from every AI-prose response (F-14 wire shape).
- `freshness` badges from read responses (honest degradation).
- user `preferences` (atmosphere, locale, reduced_motion) from `GET/PATCH /api/v1/users/me` (F-02).

**Backend Services Used:** None new (consumes existing read responses).
**Database Entities Used:** None new (reads `claims`/`citations` `evidence_weight`, user `preferences`, freshness markers).
**AI Agents Used:** None (presentation).
**External Integrations Used:** None.
**Dependencies:** F-14 (provenance to render), F-35.

---

## F-39 · Roadmap Agent

**Purpose:** Specialist A3 — the living plan: sequencing, capacity, dependencies, scenarios. Committing a re-sequence fires the commit ceremony.

**APIs Required**

1. **Dispatch a roadmap run**
   - *Endpoint:* `POST /api/v1/runs` (`agent:"roadmap"`, `task_class:"sequence_roadmap"`)
   - *Method:* POST → `202`
   - *Request Schema:* `{horizon:"Q3", constraint_refs:{capacity, dependencies, decisions, metrics}, scenario?}` + `Idempotency-Key`
   - *Response Schema:* `202` run → sequenced plan; scenarios tagged `kind:"simulated"` (violet)
   - *Auth Requirements:* `editor`+.

2. **Horizon lens (read the living plan)**
   - *Endpoint:* `POST /api/graphql` (Horizon lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash + `{horizon, stream_id?}`
   - *Response Schema:* sequenced plan with dependencies (`Claim[]`), scenarios tagged simulated
   - *Auth Requirements:* any member, ACL-trimmed.

3. **Generate a scenario (simulated, violet)**
   - *Endpoint:* `POST /api/v1/roadmaps/{id}/scenarios` · `GET /api/v1/roadmaps/{id}`
   - *Method:* POST, GET
   - *Request Schema:* `POST {what_if:Claim[]}` + `Idempotency-Key` (identical what-ifs return the same simulated result)
   - *Response Schema:* simulated scenario (`kind:"simulated"`, never committed as fact)
   - *Auth Requirements:* `editor`+ to generate; read → any member.

4. **Commit a re-sequence** → `POST /api/v1/decisions/commit` (per F-30, `source_action:"roadmap_resequence"`).

**Backend Services Used:** Agent runtime (F-17), Roadmap service (sequencing, dependency graph), Prioritization (F-40, ranking inputs), Metric-store client (F-15), Decision/commit (F-30), GraphQL gateway.
**Database Entities Used:** `roadmaps`, `releases`, `dependencies`, `decision_ledger`, metric store, scenario records.
**AI Agents Used:** **Roadmap (A3)**; **Prioritization (A4)** supplies ranking inputs; **Conductor (S1)** orchestrates.
**External Integrations Used:** **Anthropic Claude** (sequencing/scenario reasoning via Model Gateway).
**Dependencies:** F-17, F-13, F-28.

---

## F-40 · Prioritization Agent

**Purpose:** Specialist A4 — defensible ranking, trade-offs, counterfactuals (the Arena). Mandatory contrarian probe. Inputs grounded in the metric store, never invented. Committing a ranking fires the commit ceremony.

**APIs Required**

1. **Dispatch a prioritization run**
   - *Endpoint:* `POST /api/v1/runs` (`agent:"prioritization"`, `task_class:"rank_candidates"`)
   - *Method:* POST → `202`
   - *Request Schema:* `{candidate_set:[object_ref], scoring_model_id?, weights?}` + `Idempotency-Key`
   - *Response Schema:* `202` run → ranked candidates with per-candidate trade-offs, counterfactuals, **mandatory contrarian probe**, every input cited from the metric store
   - *Auth Requirements:* `editor`+.

2. **Arena lens (read the ranking)**
   - *Endpoint:* `POST /api/graphql` (Arena lens)
   - *Method:* POST
   - *Request Schema:* persisted-query hash + `{candidate_set? | ranking_id}`
   - *Response Schema:* ranked candidates, trade-offs, counterfactuals, contrarian probe; `422 metric_unavailable` → an input "unmeasurable," never invented (the literal kill of prioritization theater)
   - *Auth Requirements:* any member, ACL-trimmed.

3. **Commit a ranking** → `POST /api/v1/decisions/commit` (per F-30, `source_action:"arena_ranking"`).

**Backend Services Used:** Agent runtime (F-17), Prioritization service (ranking/scoring), Retrieval (F-13), Metric-store client (F-15, governed inputs), Decision/commit (F-30), GraphQL gateway.
**Database Entities Used:** `candidate_set`/object refs, `scoring_models` (procedural memory), metric store, `decision_ledger`, `claims`/`citations`.
**AI Agents Used:** **Prioritization (A4)** (ranking/scoring, contrarian probe, counterfactual reasoning).
**External Integrations Used:** **Anthropic Claude** (ranking reasoning via Model Gateway).
**Dependencies:** F-13, F-15, F-17.

---

## F-41 · Compliance Baseline (SOC 2 Type II) & GDPR Erasure Cascade

**Purpose:** SOC 2 Type II posture; GDPR DSAR/erasure cascade ≤24h via tombstones leaving typed "redacted" stubs so ledger auditability survives content removal.

**APIs Required**

1. **DSAR — access / erasure (async job)**
   - *Endpoint:* `POST /api/v1/dsar`
   - *Method:* POST → `202`
   - *Request Schema:* `{subject_ref, action:"access"|"erasure"}` + `Idempotency-Key` (one erasure per subject)
   - *Response Schema:* `202` job → erasure complete ≤24h, **audit chain preserved via redacted stubs**; cascade miss → reconciliation (not silent)
   - *Auth Requirements:* `admin`/DPO + **step-up MFA**.

2. **Audit verification (compliance evidence)**
   - *Endpoint:* `GET /api/v1/audit/verify` (per F-21)
   - *Method:* GET
   - *Request Schema:* `?chain=`
   - *Response Schema:* verified chain head; mismatch → integrity alert (sev-1)
   - *Auth Requirements:* `admin`/`owner`.

**Backend Services Used:** DSAR/erasure orchestrator (tombstone cascade), Audit fabric (F-21, redacted-stub preservation), Soft-delete/purge path (F-03), Tenancy (RLS).
**Database Entities Used:** `deleted_at` soft-delete columns, tombstones/redacted stubs, `decision_ledger` (auditability survives), DSAR job records.
**AI Agents Used:** None (platform/compliance).
**External Integrations Used:** **AWS S3 (WORM)** (audit anchors); source systems are notified of erasure via the cascade where applicable.
**Dependencies:** F-01, F-02, F-21, F-03 (soft-delete path).

---

## F-42 · Resilience & Kill Switches

**Purpose:** In-cell multi-AZ HA; RTO 1h / RPO ≤5 min; model-provider failover degrading Ask to a lower tier with a **visible quality badge** (honest degradation); kill switches per tenant / agent / tool / autonomy-level.

**APIs Required**

1. **Kill switch (per tenant / agent / tool / level)**
   - *Endpoint:* `POST /api/v1/ops/killswitch`
   - *Method:* POST
   - *Request Schema:* `{scope:"tenant"|"agent"|"tool"|"level", target_id?, state:"on"|"off", reason}` + `Idempotency-Key` (idempotent by `(scope, target, state)`)
   - *Response Schema:* killswitch state; lag → escalation
   - *Auth Requirements:* `owner`/on-call + **step-up MFA**.

2. **Ops status (degradation + quality-badge state)**
   - *Endpoint:* `GET /api/v1/ops/status`
   - *Method:* GET
   - *Request Schema:* none
   - *Response Schema:* `{model_gateway:{tier, degraded, quality_badge?}, degradation_state, kill_switch_states, ...}`
   - *Auth Requirements:* `admin`/`owner`.

The model-failover quality badge also rides inline on `GET /api/v1/ask/{id}/stream` and `GET /api/v1/runs/{id}/stream` (`503 provider_degraded`, stream continues) — owned by F-07/F-29/F-17, surfaced here as ops state.

**Backend Services Used:** Ops/resilience service (kill switches, status), Model Gateway (F-07, failover + quality badge), HA/replication infra.
**Database Entities Used:** `kill_switch_state` (per scope), degradation/quality-badge state, ops status.
**AI Agents Used:** None directly (it can *halt* any agent/tool/level via kill switch).
**External Integrations Used:** **Anthropic Claude / OpenAI** (failover across tiers via the Model Gateway).
**Dependencies:** F-07, F-17, F-18.

---

# Part II — Cross-Cutting Matrices & Classification

The matrices below cover the Foundation/MVP/V1 scope (F-01…F-42). They use a canonical endpoint vocabulary; SSE streams and the GraphQL lens endpoint are named explicitly because their concurrency/traffic profile differs from REST.

## 1. API Dependency Matrix

*Reads: "to call the API in the left column, the features/APIs in the right column must already exist." Every row also assumes the universal auth baseline (F-01 tenancy + F-02 identity) and the BFF contract (F-05). Those three are omitted from each row to keep the matrix legible — they are the implicit root of every dependency chain.*

| API (endpoint) | Owning feature(s) | Hard prerequisites (beyond F-01/F-02/F-05) |
|---|---|---|
| `GET /session` · `POST /stream-tickets` · `POST /session/logout` | F-02 | — (identity itself) |
| `POST/GET/PATCH /organizations` · `PUT /organizations/{id}/identity-binding` | F-01, F-02 | — |
| `POST/GET/PATCH /workspaces` | F-03 | F-01 |
| `POST/GET/PATCH/DELETE /streams` · `/streams/{id}/members` · `/lenses` · `/lenses/{id}/share` · `/workspaces/{id}/members` | F-36 | F-03, F-35 |
| `GET/PATCH /users/me` · `GET /users/{id}` · `GET /workspaces/{id}/users` | F-02 | F-06 (user projections) |
| `POST/GET/DELETE /connectors` · `/connectors/{id}/{health,coverage,backfill}` · `/connectors/oauth/callback` · `POST /webhooks/connectors/{provider}` | F-08 | F-03, F-04 |
| `GET /ingestion/quarantine` · `POST /ingestion/quarantine/{id}/release` | F-09 | F-08, F-04 |
| `POST/GET/PATCH/DELETE /products·features·epics·user-stories·requirements` | F-03 | F-01 |
| `POST/GET /documents` · `/documents/{id}/versions` | F-03 | F-01 |
| `GET /search` | F-13, F-33 | F-12, F-11, F-07, F-15 (full chain F-08→F-12) |
| `POST /ask` · `GET /ask/{id}/stream` | F-29, F-33 | F-13, F-14, F-28 |
| `GET /provenance/{id}` · `/provenance/{id}/chain` | F-14 | F-13, F-11 (provenance written at upsert) |
| `POST /metrics/query` · `GET /metrics/catalog` | F-15 | F-03 |
| `POST /runs` · `GET /runs/{id}{,/stream,/steps}` · `POST /runs/{id}/{cancel,replay}` | F-17 | F-06, F-07, F-04 |
| `GET /agents` · `GET /agents/{id}` | F-06 | F-03 |
| `GET /conversations/{id}` | F-06 | F-03 |
| `POST /approvals/{runId}` | F-18 | F-17, F-02, F-21 (hash-chain) |
| `POST /runs (prd)` · `POST/GET /prds` | F-24 | F-13, F-14, F-17, F-18, F-16, F-20 |
| `POST /runs (story)` · commit `epics/user-stories/requirements` | F-25 | F-24, F-13, F-20 |
| `POST /runs (research)` | F-27 | F-22, F-13, F-17 |
| `POST /runs (conductor)` | F-26 | F-17, F-18, F-16 |
| `POST /decisions/commit` · `/decisions/{id}/{premortem,guards}` · `GET /decisions/{id}{,/history}` | F-28, F-30 | F-21, F-03 (and F-15 for predicted_impact) |
| GraphQL Decision Sheet lens | F-28 | F-28, F-13 |
| `GET /audit/verify` | F-21 | F-03, S3/WORM |
| `POST /runs (prioritization)` · GraphQL Arena lens | F-40 | F-13, F-15, F-17 |
| `POST /runs (roadmap)` · GraphQL Horizon lens · `/roadmaps/{id}/scenarios` | F-39 | F-17, F-13, F-28 |
| GraphQL feedback-cluster lens · `GET /feedback/clusters/{id}` · `POST /feedback/clusters/{id}/thread` | F-22 | F-10, F-11, F-13, F-15 |
| `POST /diagnostic` · `GET /diagnostic/{id}{,/stream}` | F-23 | F-22, F-08, F-09 |
| `POST /sync/push` · `GET /sync/{id}` · `/sync/state` | F-31 | F-08, F-19, F-24/F-25 |
| `GET /tide{,/stream}` · `POST /tide/{id}/ack` | F-37 | F-05 (SSE); F-43 for full value (V2) |
| GraphQL Brief lens · `GET/PATCH /notifications/preferences` | F-32 | F-28, F-14, F-13, F-04 |
| GraphQL Stream Canvas lens | F-35 | F-05, F-03 |
| GraphQL usage lens · `GET /billing/meters` | F-34 | F-06, F-21 |
| `GET/PATCH /admin/procedural-memory/{kind}` | F-16 | F-03, F-12, F-06 |
| `POST /dsar` | F-41 | F-01, F-02, F-21, F-03 |
| `POST /ops/killswitch` · `GET /ops/status` | F-42, F-01 | F-07, F-17, F-18 |
| `POST /api/graphql` (the lens endpoint itself) | F-05 | F-01, F-03 |
| `GET /jobs/{id}{,/stream}` · `POST /jobs/{id}/cancel` (async grammar) | F-05 | F-04, F-17 |

**The longest dependency pole** (matching the Feature Inventory's): `GET /search` and the Ask path bottom out on the strictly-sequential ingestion→retrieval chain — `connectors (F-08) → quarantine/screening (F-09) → extraction (F-10) → entity-resolution (F-11) → index fan-out (F-12) → retrieval (F-13)` — plus `metrics/query (F-15)` and `provenance (F-14)`. Every generative `POST /runs` variant additionally requires the agent runtime trio `F-17 + F-18 + F-19` and the eval gate `F-20`.

---

## 2. Feature-to-API Mapping

*The compact index: each in-scope feature → the APIs it requires (○ = the feature's own primary surface; → = an API owned elsewhere that the feature consumes). Async-job grammar (`/jobs/{id}…`) is implied wherever a `202`/`POST /runs`/`POST /diagnostic` appears.*

| Feature | Primary APIs (○ own · → consumed) |
|---|---|
| **F-01** RLS/Tenancy | ○ `POST/GET/PATCH /organizations` · ○ `GET /session` · ○ `POST /ops/killswitch` · (force-injection on **all** endpoints) |
| **F-02** Identity/Clerk | ○ `GET /session` · ○ `POST /stream-tickets` · ○ `POST /session/logout` · ○ `PUT /organizations/{id}/identity-binding` · ○ `GET/PATCH /users/me` · ○ `GET /users/{id}` · ○ `GET /workspaces/{id}/users` |
| **F-03** Core Schema/Hierarchy | ○ `POST/GET/PATCH /workspaces` · ○ `POST/GET/PATCH/DELETE /products·features·epics·user-stories·requirements` · ○ `POST/GET /documents` · ○ `/documents/{id}/versions` |
| **F-04** Outbox/Events | (no client endpoint) → powers all SSE (`/runs/{id}/stream`, `/tide/stream`, `/jobs/{id}/stream`) + projection freshness |
| **F-05** BFF/3-protocol | ○ `POST /api/graphql` · ○ `GET /jobs/{id}{,/stream}` · ○ `POST /jobs/{id}/cancel` · (`Idempotency-Key`, error envelope, `PMOS-Version`, cursor pagination on all) |
| **F-06** AI Schema Spine | ○ `GET /agents{,/{id}}` · ○ `GET /runs/{id}{,/steps}` · ○ `GET /conversations/{id}` |
| **F-07** Model Gateway | (internal) → `GET /ops/status` (degradation) · inline `503 provider_degraded` badge on Ask/run streams |
| **F-08** Connectors | ○ `POST/GET/DELETE /connectors` · ○ `/connectors/{id}/{health,coverage,backfill}` · ○ `/connectors/oauth/callback` · ○ `POST /webhooks/connectors/{provider}` |
| **F-09** Screening | ○ `GET /ingestion/quarantine` · ○ `POST /ingestion/quarantine/{id}/release` |
| **F-10** Extraction | (internal pipeline) → observable via `GET /runs/{id}/steps` (metered) |
| **F-11** Entity Resolution | (internal pipeline) → writes provenance/`read_principals` read by F-14/F-13/F-22 |
| **F-12** Index Fan-Out | (internal) → queried only via `GET /search` + Ask retrieval |
| **F-13** Retrieval v1 | ○ `GET /search` · → `POST /ask` (F-29), `POST /metrics/query` (F-15) |
| **F-14** Claim[]/Provenance | ○ `GET /provenance/{id}` · ○ `/provenance/{id}/chain` · (`Claim[]` on every AI-prose response) |
| **F-15** Metric Store | ○ `POST /metrics/query` · ○ `GET /metrics/catalog` |
| **F-16** Memory Plane | ○ `GET/PATCH /admin/procedural-memory/{kind}` · → `/runs/{id}/steps` (working), ledger (episodic), search (semantic) |
| **F-17** Agent Runtime | ○ `POST /runs` · ○ `GET /runs/{id}{,/stream,/steps}` · ○ `POST /runs/{id}/{cancel,replay}` |
| **F-18** Policy/Tokens | ○ `POST /approvals/{runId}` · (`403 capability_denied`/`409 approval_required` across run/sync) |
| **F-19** Tool Service | (internal) → enforces `POST /sync/push` (F-31), `POST /metrics/query` (F-15) |
| **F-20** Eval Harness | ○ `POST /api/graphql` (quality/edit-distance lens) · (blocks drafts on F-24/F-25) |
| **F-21** Audit Fabric | ○ `GET /audit/verify` · (hash-chain append inside commit/approval/billing) |
| **F-22** Feedback Intelligence | ○ GraphQL feedback-cluster lens · ○ `GET /feedback/clusters/{id}` · ○ `POST /feedback/clusters/{id}/thread` · ○ `POST /runs (research)` |
| **F-23** Diagnostic | ○ `POST /diagnostic` · ○ `GET /diagnostic/{id}{,/stream}` |
| **F-24** PRD Agent | ○ `POST /runs (prd)` · ○ `POST/GET /prds` · → `GET /runs/{id}/stream`, `POST /approvals/{runId}` |
| **F-25** Story Agent | ○ `POST /runs (story)` · ○ commit `epics/user-stories/requirements` · → `POST /approvals/{runId}` |
| **F-26** Conductor | ○ `POST /runs (conductor)` (parent/child) · → `GET /runs/{id}{,/stream,/steps}` |
| **F-27** Research Agent | ○ `POST /runs (research)` · → `GET /runs/{id}/stream` |
| **F-28** Decision Ledger | ○ `GET /decisions/{id}{,/history}` · ○ GraphQL Decision Sheet lens · → `GET /audit/verify` |
| **F-29** Ask-the-Brain | ○ `POST /ask` · ○ `GET /ask/{id}/stream` · → `GET /conversations/{id}`, `GET /provenance/{id}` |
| **F-30** Commit Ceremony | ○ `POST /decisions/commit` · ○ `POST /decisions/{id}/premortem` · ○ `POST /decisions/{id}/guards` |
| **F-31** Living Sync v1 | ○ `POST /sync/push` · ○ `GET /sync/{id}` · ○ `GET /sync/state` |
| **F-32** Brief + Notify | ○ GraphQL Brief lens · ○ `GET/PATCH /notifications/preferences` |
| **F-33** The Line | → `GET /search` (Go) · → `POST /ask`+stream (Ask) · → `POST /runs` (Do) |
| **F-34** Metering/Billing | ○ `GET /billing/meters` · ○ GraphQL usage lens |
| **F-35** Canvas/Altitudes | ○ GraphQL Stream Canvas lens · → object reads (F-03/F-28/F-14) |
| **F-36** Streams/Lenses | ○ `POST/GET/PATCH/DELETE /streams{,/members}` · ○ `/lenses{,/share}` · ○ `/workspaces/{id}/members` |
| **F-37** Tide/Meridian Bar | ○ `GET /tide{,/stream}` · ○ `POST /tide/{id}/ack` |
| **F-38** Design System | (no endpoint) → renders `Claim[]` evidence_weight, freshness, `users/me` preferences |
| **F-39** Roadmap Agent | ○ `POST /runs (roadmap)` · ○ GraphQL Horizon lens · ○ `/roadmaps/{id}/scenarios` · ○ `GET /roadmaps/{id}` · → `POST /decisions/commit` |
| **F-40** Prioritization Agent | ○ `POST /runs (prioritization)` · ○ GraphQL Arena lens · → `POST /decisions/commit`, `POST /metrics/query` |
| **F-41** Compliance/GDPR | ○ `POST /dsar` · → `GET /audit/verify` |
| **F-42** Resilience/Kill | ○ `POST /ops/killswitch` · ○ `GET /ops/status` |

---

## 3. Agent-to-API Mapping

*Which APIs each AI agent dispatches against, executes through, or supplies. Every agent runs through the universal run lifecycle `POST /api/v1/runs` + `GET /runs/{id}{,/stream,/steps}` (F-17) and calls the Model Gateway (F-07) at runtime — listed once here, not repeated per row. "Tools" are reached only through the governed tool service (F-19) under a capability token (F-18).*

| Agent | Dispatched via (`task_class`) | Reads / tools (consumed APIs) | Writes / approval gate | In-scope features |
|---|---|---|---|---|
| **S1 Conductor** | `POST /runs (conductor)` — parent + child runs | child `POST /runs` for every specialist; memory lens (F-16) | submits review package to human; child writes gated by `POST /approvals/{runId}` | F-26 |
| **S2 Sentinel** | (V2 engine) — feeds the Tide | `GET /search`, metric store, ledger | emits `tide_item`/`interrupt` (F-37) | F-37 (ranking ships MVP; full engine = F-43, V2) |
| **S3 Archivist** | (V2) | mines `decision_candidates` (F-10) → ledger | proposes ledger entries (F-28) | F-16/F-28 hooks (full = F-46, V2) |
| **A1 Strategist** | (V2) — powers Pre-Mortem | metric store, ledger, priors | `POST /decisions/{id}/premortem` scaffold (Year-1) | F-30 premortem scaffold |
| **A2 Research** | `POST /runs (research, synthesize_feedback)` | `GET /search` (F-13), feedback lens (F-22), `POST /metrics/query` (F-15) | L1 read-only — **no** approval token needed | F-22, F-23, F-27 |
| **A3 Roadmap** | `POST /runs (roadmap, sequence_roadmap)` | Horizon lens, `POST /metrics/query`, ledger, dependencies | re-sequence → `POST /decisions/commit` (F-30) | F-39 |
| **A4 Prioritization** | `POST /runs (prioritization, rank_candidates)` | Arena lens, `POST /metrics/query` (governed inputs), `GET /search` | ranking → `POST /decisions/commit` (F-30) | F-40 |
| **A5 PRD** | `POST /runs (prd, draft_prd)` | `GET /search`, `POST /metrics/query`, provenance (F-14) | L2 — `POST /approvals/{runId}` → `POST /prds` | F-24 |
| **A6 Story Writing** | `POST /runs (story, write_stories)` | `GET /search`, PRD doc, gold standards (F-20) | L2 — `POST /approvals/{runId}` → commit `epics/user-stories/requirements`; external push via `POST /sync/push` + tool service (F-19/F-31) | F-25, F-31 |
| **A7 Analytics** | (V2 `POST /runs (analytics)`) | `POST /metrics/query` (the governed-number origin, F-15) | arms/closes outcome windows (V2) | F-15 governed numbers (full = F-48, V2) |
| **A8 Release** | (V2 `POST /runs (release)`) | readiness gates, metric store | `POST /releases/{id}/readiness` (V2) | (V2; Year-1 seam only) |
| **Pipeline models** (screening / extraction / entity-resolution / embedding / rerank) | run on the ingestion event chain (not the eleven named agents) | Model Gateway tiers (F-07) | write typed atoms + provenance (F-10/F-11), index (F-12) | F-09, F-10, F-11, F-12, F-13 |

*Note on Year-1 scope:* Sentinel, Archivist, Strategist, Analytics, and Release are fully realized as agents in **V2** (F-43–F-49). Within the Foundation/MVP/V1 scope, they appear only as the seams listed above (the Tide ranking, the premortem scaffold, the governed-number origin). The five **fully-active Year-1 agents** are Conductor, Research, PRD, Story, and (for the living plan) Roadmap + Prioritization.

---

## 4. Service-to-API Mapping

*Which backend service owns / backs each API. The **BFF (F-05)** fronts every endpoint (JWKS verify, TenantContext, idempotency, rate limit, error envelope) and is omitted per row. Datastores: PostgreSQL 16 (system of record), Qdrant (vectors, retrieval-service-only), Redis (cache + Streams), S3/WORM (audit anchors), Supabase Storage (blobs).*

| Backend service | APIs it owns / backs |
|---|---|
| **Tenancy service** | `POST/GET/PATCH /organizations`; force-injection of `organization_id` into Postgres RLS + Qdrant filter on **all** reads; workspace stamping |
| **Identity service (Clerk-backed)** | `GET /session`, `POST /session/logout`, `POST /stream-tickets`, `PUT /organizations/{id}/identity-binding`, `GET/PATCH /users/me`, `GET /users/{id}`, `GET /workspaces/{id}/users` |
| **Hierarchy / domain service** | `POST/GET/PATCH/DELETE /products·features·epics·user-stories·requirements`, `POST/GET /documents`, `/documents/{id}/versions`, `POST/GET /prds`, `POST/GET/PATCH /workspaces` |
| **Stream / Lens service** | `/streams{,/members}`, `/lenses{,/share}`, `/workspaces/{id}/members` |
| **Outbox relay (F-04)** | (no endpoint) — transport beneath all SSE + projection freshness |
| **GraphQL gateway (F-05)** | `POST /api/graphql` (Brief, Stream Canvas, Decision Sheet, Horizon, Arena, feedback-cluster, usage, quality lenses) |
| **Async/job orchestration (F-05)** | `GET /jobs/{id}{,/stream}`, `POST /jobs/{id}/cancel` |
| **Connector SDK / ingestion service** | `POST/GET/DELETE /connectors`, `/connectors/{id}/{health,coverage,backfill}`, `/connectors/oauth/callback`, `POST /webhooks/connectors/{provider}`, `GET /ingestion/quarantine`, `POST /ingestion/quarantine/{id}/release` |
| **Extraction / entity-resolution / index workers** | (internal pipeline; metered to `agent_runs`, observable via `/runs/{id}/steps`) |
| **Retrieval service (sole Qdrant path)** | `GET /search`; backs the Ask retrieval path and the canvas/Brief lenses |
| **Ask / QA service** | `POST /ask`, `GET /ask/{id}/stream` |
| **Provenance resolver** | `GET /provenance/{id}`, `/provenance/{id}/chain` |
| **Metric-store service (F-15)** | `POST /metrics/query`, `GET /metrics/catalog`; the governed-number origin for every claim |
| **Agent runtime (NestJS + BullMQ, F-17)** | `POST /runs`, `GET /runs/{id}{,/stream,/steps}`, `POST /runs/{id}/{cancel,replay}`; `GET /agents{,/{id}}`, `GET /conversations/{id}` |
| **Policy engine (F-18)** | `POST /approvals/{runId}`; issues capability tokens; raises `capability_denied`/`approval_required` |
| **Governed tool service (F-19)** | (internal) — executes external writes for `POST /sync/push`; verifies tokens; rejects evidence-sourced sensitive args |
| **Eval harness (F-20)** | `POST /api/graphql` (quality/edit-distance lens); release-gate that blocks drafts |
| **Audit-fabric service (F-21)** | `GET /audit/verify`; hash-chain append inside commit/approval/billing; hourly WORM anchor |
| **Feedback Intelligence service (F-22)** | feedback-cluster lens, `GET /feedback/clusters/{id}`, `POST /feedback/clusters/{id}/thread` |
| **Diagnostic orchestrator (F-23)** | `POST /diagnostic`, `GET /diagnostic/{id}{,/stream}` |
| **Decision / ledger service (F-28/F-30)** | `POST /decisions/commit`, `/decisions/{id}/{premortem,guards}`, `GET /decisions/{id}{,/history}`, Decision Sheet lens |
| **Prioritization service (F-40)** | `POST /runs (prioritization)`, Arena lens |
| **Roadmap service (F-39)** | `POST /runs (roadmap)`, Horizon lens, `/roadmaps/{id}/scenarios`, `GET /roadmaps/{id}` |
| **Living Sync service (F-31)** | `POST /sync/push`, `GET /sync/{id}`, `/sync/state` |
| **Brief renderer / Notify service (F-32)** | Brief lens, `GET/PATCH /notifications/preferences` |
| **Line / command service (F-33)** | Go/Ask/Do intent routing into `GET /search`, `POST /ask`, `POST /runs` |
| **Metering / billing service (F-34)** | `GET /billing/meters`, usage lens |
| **Memory-plane service (F-16)** | `GET/PATCH /admin/procedural-memory/{kind}` |
| **Tide / notification service (F-37)** | `GET /tide{,/stream}`, `POST /tide/{id}/ack` |
| **Model Gateway (F-07)** | (internal) — `GET /ops/status` degradation state; inline `503 provider_degraded` badge |
| **Ops / resilience service (F-42)** | `POST /ops/killswitch`, `GET /ops/status` |
| **DSAR / compliance service (F-41)** | `POST /dsar`; cascade preserving redacted stubs |

---

## 5. API Classification

### 5.1 Shared APIs

*Endpoints consumed by many features — the load-bearing surfaces. A change here ripples widely; they deserve the strictest contract stability and the most test coverage.*

| Shared API | Consumed by | Why it is shared |
|---|---|---|
| `POST /api/v1/runs` + `GET /runs/{id}{,/stream,/steps}` | every agent feature (F-22, F-23, F-24, F-25, F-26, F-27, F-39, F-40) + the Line "Do" (F-33) | the **universal run lifecycle** — all eleven agents dispatch here |
| `POST /api/v1/approvals/{runId}` | F-24, F-25, F-31, F-39, F-40 (any L2 write) | the **single L2 human gate**; issues every capability token |
| `POST /api/v1/decisions/commit` | F-28, F-30, F-39 (re-sequence), F-40 (ranking) | the **commit ceremony** — the byproduct write that fills the ledger from PRD/Arena/Roadmap actions |
| `GET /api/v1/search` | F-13, F-33 (Go), and as a tool for F-22/F-24/F-25/F-27/F-39/F-40 | the **retrieval substrate** under navigation and every grounded generation |
| `POST /api/v1/metrics/query` | F-15, F-22, F-24, F-28, F-29, F-39, F-40 (any number) | the **only governed origin of any number** ("numbers are tools, not text") |
| `GET /api/v1/provenance/{id}` | F-14, F-29, F-22, F-32, F-35 (every cited surface) | resolves **every** Claim's evidence; the product's signature interaction |
| `POST /api/graphql` (the lens endpoint) | F-22, F-28, F-32, F-34, F-35, F-39, F-40, F-20 | the **single GraphQL surface** for all multi-resource lenses |
| `GET /jobs/{id}{,/stream}` · `POST /jobs/{id}/cancel` | every slow op (runs, diagnostic, backfill, dsar, sync) | the **one async-job grammar** |
| `POST /api/v1/stream-tickets` | F-29, F-37, F-17, F-23 (every SSE consumed by `EventSource`) | the shared SSE-auth primitive |
| `GET /api/v1/session` | every client surface (render-time authority) | the shared authority/TenantContext resolver |

### 5.2 Critical APIs

*The loop or the platform cannot function (or cannot be trusted) without these. Outage or correctness failure here is a sev-0/sev-1. Most are also Shared; criticality is about consequence, not breadth.*

| Critical API | Criticality |
|---|---|
| Force-injected TenantContext on **every** endpoint (`GET /session` + RLS context) | **existential** — the cross-tenant-leak defense (threat #1); failure = sev-0 |
| `POST /api/v1/approvals/{runId}` + the capability-token path (F-18/F-19) | the structural guarantee against prompt-injection-driven writes (threat #2); CI attack-success target 0 |
| `POST /api/v1/decisions/commit` (hash-chained, signature-verified) | the ledger is the system of record for *decisions*; financial-grade idempotency; tamper-evidence (threat #5) |
| `GET /api/v1/search` + the retrieval chain (F-08→F-13) | the wedge and every grounded answer depend on it; the longest build pole |
| `POST /api/v1/metrics/query` | a fabricated number destroys CFO trust; the governed origin is non-negotiable |
| `GET /api/v1/provenance/{id}` | "every generated sentence is a contract"; uncitable prose breaks the core promise (P4's veto) |
| `POST /api/v1/ask` + `/ask/{id}/stream` | the org-wide Brain and the ubiquity/expansion engine (free viewers) |
| `POST /webhooks/connectors/{provider}` (HMAC-verified, deduped) | the live-freshness ingress; an unverified webhook reaching the pipeline is a security failure |
| `GET /api/v1/audit/verify` + the hash-chain append | insurance-grade audit; chain-head mismatch = integrity alert (sev-1) |
| `POST /api/v1/ops/killswitch` | the operator's emergency stop per tenant/agent/tool/level; prerequisite for granting any autonomy |
| `POST /api/graphql` (Brief / canvas / sheet lenses) | the primary read surface for ~92% read-mostly users; "the system speaks first" |

### 5.3 High-Traffic APIs

*Highest request volume or tightest latency budget → where caching, projections, complexity budgets, rate-limit classes, and the CI performance gates concentrate. Design envelope: ~1M users (~925k read-mostly viewers), ~50M ingestion events/day, ~3M agent tasks/day, ~50k concurrent peak.*

| High-traffic API | Volume / budget driver | CI budget & mitigation |
|---|---|---|
| `GET /api/v1/search` (Line "Go") | every navigation keystroke-class action; viewers included | **Go <50ms**; int8 quantization + tiered pre-filter; no LLM in hot path; `read` class 600/min |
| `POST /api/graphql` (Brief, canvas, sheet) | ~92% read-mostly viewers hit precomputed projections | **Peek <100ms · Sheet <150ms · cold Brief <1.5s**; persisted queries + complexity budget (cost ≤1000) + DataLoader; `graphql` class 20k cost-units/min |
| `GET /api/v1/provenance/{id}` | clicked on **every** Claim across every cited surface | **Provenance Lens <400ms**; chunk id = Qdrant point id (no mapping table); `read` class |
| `POST /api/v1/ask` + `/ask/{id}/stream` | the ubiquity engine; all viewers | **first token <700ms**; interactive prioritized over batch lanes; Model Gateway tiering + failover badge; `ask` class 30/min |
| `POST /webhooks/connectors/{provider}` | ~50M ingestion events/day | `ingest/webhook` class with per-connector backoff; idempotent dedupe on `external_event_id`; three priority lanes |
| `GET /api/v1/tide/stream` (SSE) | one persistent stream per active session (~50k concurrent) | SSE heartbeats 15s, 15-min server termination + resume; one Tide SSE per session; outbox-driven fan-out |
| `POST /api/v1/runs` + `/runs/{id}/stream` | ~3M agent tasks/day | `run` class 60/min; **batch lanes** isolate autonomous (Overnight/backfill) work from interactive budgets |
| `POST /api/v1/metrics/query` | called as a tool by every number-emitting generation **and** by clients for inspection | read-like cache by `(metric_id, dims, window)`; metered (cost + provenance) |
| Canvas lens reads (Stream Canvas) | pan/zoom interactions | **≥60fps**; complexity budget + pagination; client renders graph state, no per-frame API call |

### 5.4 AI-Heavy APIs

*APIs whose execution path runs frontier-model inference (and therefore carries token cost, latency variance, the eval gate, and the failover quality badge). These are the metered surfaces and the ones whose quality the eval harness (F-20) gates.*

| AI-heavy API | AI work on the path | Governing controls |
|---|---|---|
| `POST /runs (prd, draft_prd)` (F-24) | claim-grounded PRD generation + **mandatory contrarian probe** | eval gate (F-20) blocks sub-bar drafts; L2 approval; edit-distance logged; `Claim[]` + metric-store numbers |
| `POST /runs (story, write_stories)` (F-25) | engineer-grade epic/story/AC generation | eval gate + gold standards; **kill/pivot trigger** >30% edit-distance; L2 approval (Priya's bar) |
| `POST /runs (research, synthesize_feedback)` (F-22, F-27) | feedback synthesis + summarization with citations + contrarian probe | L1 read-only; groundedness verification; contrarian probe mandatory |
| `POST /runs (prioritization, rank_candidates)` (F-40) | ranking/scoring + counterfactuals + contrarian probe | every input metric-store-cited; `metric_unavailable` → "unmeasurable," never invented |
| `POST /runs (roadmap, sequence_roadmap)` (F-39) | sequencing/scenario reasoning | scenarios forced `kind:"simulated"` (violet); governed metric inputs |
| `POST /runs (conductor)` (F-26) | planning/decomposition/delegation across child runs | parent/child token scoping; assembles human review package |
| `POST /ask` + `/ask/{id}/stream` (F-29) | claim-grounded QA, rerank, LLM-select, generation | groundedness verification; honest abstention (≥95% metric); numbers-as-tools; failover badge |
| `GET /search` (F-13) | hybrid fusion + cross-encoder rerank (LLM-select tier) | **no generative LLM in the <50ms hot path**; LLM-select reserved for deeper tiers |
| `POST /diagnostic` (F-23) | feedback synthesis + coverage estimation on prospect data | bulk lane (never blocks live tenants); trial-scoped + purged |
| `POST /decisions/{id}/premortem` (F-30) | synthetic-stakeholder pre-mortem (V2 Strategist; Year-1 scaffold) | Year-1 returns a structured scaffold; full synthesis = V2 |
| Ingestion extraction (F-10, internal) | typed-atom extraction via cheap-first cascade | ~70% never touch an LLM; metered to `agent_runs`; injection-screened upstream |
| Index embedding (F-12, internal) | `text-embedding-3-large` @ 3072d | `content_hash` prevents redundant re-embedding; batch lane |

**Reading the AI-heavy set together:** every generative `POST /runs` variant shares one shape — frontier inference behind the Model Gateway (F-07), metered to `agent_runs` (F-06), gated by the eval harness (F-20), grounded in retrieval (F-13) + the metric store (F-15), emitting `Claim[]` (F-14), and (for any write) gated by an L2 approval that issues a capability token (F-18) verified at the governed tool service (F-19). That common spine is why these five-to-seven endpoints concentrate the platform's cost, latency variance, and trust-critical quality controls.

---

## 6. How to use this inventory

- **Building a feature?** Read its Part I section top to bottom: it names every endpoint you must implement or consume, the exact request/response shape, the auth floor, and the services/entities/agents/integrations you will touch. The **Dependencies** line tells you what must already exist.
- **Sequencing the build?** Cross-reference §1 (Dependency Matrix) with the Feature Inventory's wave structure. The ingestion→retrieval chain (F-08→F-13) plus the agent-runtime trio (F-17/F-18/F-19) and the eval gate (F-20) are the prerequisites that unlock the MVP loop.
- **Hardening or load-testing?** Start from §5.2 (Critical), §5.3 (High-Traffic), and §5.4 (AI-Heavy) — they tell you where a failure is existential, where latency budgets bite, and where token cost and quality gates live.
- **Changing a contract?** Check §5.1 (Shared) first: the run lifecycle, the commit ceremony, the approval gate, search, metrics/query, provenance, and the GraphQL lens endpoint each have many consumers, and `/v1` + `PMOS-Version` (additive-only) are the only safe ways to evolve them.

*This inventory covers Foundation (F-01…F-21), MVP (F-22…F-34), and V1 (F-35…F-42) against Year-1 implementation as authoritative. Endpoints whose full capability is a V2/Year-3 target (bidirectional Living Sync + Story L3, Release/Launch Control, the Analytics outcome agent, the public Platform API, the full Sentinel/Archivist/Strategist agents) are noted as forward-compatible seams so engineering can build the Year-1 contract without building the end-state prematurely.*
