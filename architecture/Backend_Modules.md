# PMOS — Backend Modules

### PMOS Decomposed into Implementation-Ready Backend Modules

**Status:** Backend Module Specification v1.0 · **Audience:** backend engineers, AI/agent engineers, platform/infra engineers, tech leads
**Sources of truth (in precedence order):** `PMOS_MASTER_SPEC_Final.md` (Constitution v1.0) → `Feature_Inventory.md` (F-01…F-61) → `API_Design.md` → `API_Inventory.md` → `User_Flows.md` → `Frontend_Modules.md`.
**Scope of this document:** every **Foundation (F-01–F-21)**, **MVP (F-22–F-34)**, and **V1 (F-35–F-42)** feature, expressed as a backend module an engineer can implement without ambiguity. V2/Future features (F-43–F-61) appear only as *forward-compatibility seams* on the modules they extend; they are not specified here.

---

## 0. How to read this document

This is a build document. Each module section is a fixed template so the same questions are answered for every module:

> **Module Name · Feature ID(s) · Group · Purpose · Domain Models · Database Tables Owned · Services · Controllers · Events Published · Events Consumed · Queue Workers · AI Agents Used · External Integrations · Dependencies · Security Responsibilities · Complexity · Recommended Build Order**

Three rules govern everything below, inherited from the spec and the API constitution:

1. **The BFF is the only client-facing surface.** No module exposes a controller a client reaches except through the BFF (F-05). Internal service-to-service calls are named but are never client-reachable. "Controllers" listed per module are the HTTP/GraphQL/SSE handlers the BFF routes to that module's service.
2. **`organization_id` (and `workspace_id`) on every content row; TenantContext is mandatory.** Every table a module owns carries `organization_id`. Every query runs inside a tenant-scoped transaction. A module that cannot resolve TenantContext refuses to touch data. This is the load-bearing isolation invariant (TD-1).
3. **The outbox is the only way state changes become events.** No module emits an event except by writing an `outbox` row in the same Postgres transaction as the state change (TD-2). All "Events Published" below are outbox rows; the relay (F-04) transports them over Redis Streams (Year-1) with a Kafka/MSK seam.

**Two principals, always.** Human authority is a per-request session claim (RBAC × ABAC × source-ACL trim). Agent authority is a consumable capability token bound to `(run_id, task_class, approval_event, ttl=5min)`. A module that executes an agent-invoked action verifies the token at the governed tool service (F-19); it never trusts an agent's ambient identity.

**Conventions used in every module:**
- **DB conventions:** PostgreSQL 16; UUIDv7 PKs everywhere (time-ordered, doubles as the Qdrant point id — no mapping table); `timestamptz` always; `created_at`/`updated_at` (latter by trigger); soft delete via `deleted_at` (30-day trash → purge = GDPR erasure path); `organization_id` + `workspace_id` on every content row; `(context_type, context_id)` polymorphism only for genuinely "to-anything" edges (comments, tags, links, conversations).
- **Auditable tables** are append-only: `row_hash = H(prev_hash ‖ canonical_payload)`, `UPDATE`/`DELETE` revoked at the role level, hourly chain-head anchored to WORM S3 (F-21).
- **AI prose is `Claim[]`** (`{text, citations[], kind, confidence}`) at the wire level — never a bare string. **Numbers are tools, not text** — any quantitative claim originates from a governed metric-store call (F-15).
- **Datastores referenced:** PostgreSQL 16 (system of record), Qdrant (derived vectors, reachable only from the retrieval service), Redis (cache + Streams + BullMQ), S3/WORM (audit anchors + archives), Supabase Storage (user-facing blobs), Clerk (identity), Anthropic Claude + OpenAI embeddings (behind the Model Gateway).

---

## Module group overview

PMOS's backend is organized into **eight service groups**. Each module belongs to exactly one group; cross-group dependencies are explicit in each module's **Dependencies** field.

| # | Group | Modules | Role in the system |
|---|---|---|---|
| 1 | **Platform Services** | M-01 Tenancy · M-04 Event Backbone · M-05 BFF & API Gateway · M-07 Model Gateway · M-21 Audit Fabric · M-34 Metering & Billing · M-41 Compliance · M-42 Resilience & Kill Switches | The substrate everything sits on: isolation, eventing, the single client surface, model access, the audit spine, billing meters, compliance, and operational safety. |
| 2 | **Identity Services** | M-02 Identity & Access · M-36 Streams, Lenses & Membership | Who the human is, what they may see, and the human-curated containers (Streams) + shareable views (Lenses). |
| 3 | **Knowledge Platform** | M-03 Core Persistence & Product Hierarchy · M-08 Connectors & Ingestion Ingress · M-09 Screening · M-10 Signal Extraction · M-11 Entity Resolution & Graph Upsert · M-12 Index Fan-Out · M-13 Hybrid GraphRAG Retrieval · M-14 Claim[] & Provenance · M-15 Governed Metric Store · M-16 Memory Plane | The system of record + the ingestion→retrieval pipeline that turns hostile raw content into a cited, permission-safe, queryable graph. The longest build pole. |
| 4 | **AI Platform** | M-06 AI Schema Spine · M-17 Agent Runtime · M-18 Policy Engine & Capability Tokens · M-19 Governed Tool Service · M-20 Eval Harness · M-26 Conductor · M-27 Research Agent | The agent execution substrate: the audit/cost spine, stateless replayable runs, the autonomy contract, the tool enforcement point, the release-gating eval harness, and the two always-on system/specialist agents. |
| 5 | **Product Intelligence** | M-22 Feedback Intelligence · M-23 Diagnostic · M-24 PRD Agent · M-25 Story Agent · M-29 Ask-the-Brain · M-33 The Line | The wedge and the loop-closers: feedback synthesis, the GTM diagnostic, evidence-native artifact generation, the org-wide brain, and the command interface. |
| 6 | **Planning Services** | M-28 Decision Ledger · M-30 Commit Ceremony · M-39 Roadmap Agent · M-40 Prioritization Agent | The decision layer: the first-class decision object, the ceremony that fills it, and the agents that produce defensible plans and rankings. |
| 7 | **Release Services** | M-31 Living Sync v1 · M-32 Standing Brief & Notify | Coherence with execution tools (one-way spec→Jira/Linear/ADO) and continuously-current reporting. (Full Launch Control + outcome loop are V2 seams here.) |
| 8 | **Administration Services** | M-35 Meridian Canvas Lens · M-37 Tide & Meridian Bar · M-38 Design System Backend Support | The read/notification surfaces that make the loop usable: the canvas lens projections, calm ranked notifications, and the per-reader i18n/theming support endpoints. |

> **Module numbering** mirrors feature IDs (M-NN ↔ F-NN) for traceability. A module may own more than one feature where the spec fuses them (e.g. M-28/M-30 Decision Ledger + Commit Ceremony, M-37 Tide + Meridian Bar). The build sequence at the end (§Recommended Backend Build Sequence) reorders them by dependency, not by number.

---

# Group 1 — Platform Services

*The substrate. Nothing in any other group can ship until the Wave-0 members of this group exist. These modules implement the §20 critical-path bedrock: isolation, eventing, the single client surface, model access, and the audit spine.*

---

## M-01 · Tenancy & Row-Level Security

| | |
|---|---|
| **Feature ID(s)** | F-01 |
| **Group** | Platform Services |

**Purpose.** Enforce the existential isolation invariant: shared database, shared schema, PostgreSQL Row-Level Security. Owns the Organization (the hard RLS boundary), the per-request TenantContext machinery, and force-injection of `organization_id` into every Postgres query and every Qdrant payload filter. The data layer **refuses any query lacking a TenantContext**.

**Domain Models.** `Organization` (residency_region, plan, identity_binding ref, kill_switch_state), `TenantContext` (organization_id, workspace_id, user_id, roles[], abac_attrs — request-scoped, not persisted), `RLSPolicy` (the canonical policy applied to every content table).

**Database Tables Owned.** `organizations` (org-level row; residency write-once). Owns the *cross-cutting column contract* (`organization_id` + `workspace_id` on every content table) and the canonical RLS policy + `FORCE ROW LEVEL SECURITY` applied to all content tables platform-wide — but the content tables themselves are owned by their domain modules.

**Services.** `TenancyService` (org CRUD, residency validation, workspace stamping), `TenantContextResolver` (resolves `(org, ws, user, roles, abac)` from a verified JWT and opens a tenant-scoped tx via `SET LOCAL app.current_org_id`), `RLSEnforcementMiddleware` (refuses context-less queries), `QdrantFilterInjector` (force-injects `organization_id` into every retrieval filter — never from request params).

**Controllers.** `POST /api/v1/organizations`, `GET /api/v1/organizations/{orgId}`, `PATCH /api/v1/organizations/{orgId}`. (Force-injection runs as middleware on *every* endpoint platform-wide, not as its own controller.)

**Events Published.** `organization.created`, `organization.updated`, `organization.residency_set`.

**Events Consumed.** None (bedrock).

**Queue Workers.** `cross-tenant-probe-worker` (nightly: probes for any cross-tenant read; any hit = sev-0 incident).

**AI Agents Used.** None (platform).

**External Integrations.** Clerk (org↔IdP binding handle, owned with M-02); AWS infra for residency-region provisioning.

**Dependencies.** PostgreSQL 16 provisioned. None other (this is bedrock).

**Security Responsibilities.** Threat #1 (cross-tenant exposure — existential). Owns enforcement layer #1 of the three independent layers: RLS + `FORCE ROW LEVEL SECURITY` + force-injected `organization_id`. Org = hard boundary; workspace = soft app-layer boundary (deliberate defense-in-depth split). Background workers run `BYPASSRLS` and are code-review-gated to filter `organization_id` explicitly. Residency region immutable post-creation via public API.

**Complexity.** High.

**Recommended Build Order.** **1** (bedrock; literally every row and query depends on it).

**Forward-compatibility seam.** `organization_id`-on-every-row makes the Year-2 cell migration an event-replay + router-flip, not a rewrite (TD-1). Cell stamp ships day one (it is how staging works).

---

## M-04 · Event Backbone (Transactional Outbox)

| | |
|---|---|
| **Feature ID(s)** | F-04 |
| **Group** | Platform Services |

**Purpose.** Guarantee that no event is ever emitted without its state change committed in the same transaction (the outbox invariant), and transport those events to projection builders and async workers. Redis Streams transport in Year-1 behind a relay worker; explicitly Kafka/MSK-swappable.

**Domain Models.** `OutboxRecord` (aggregate_type, aggregate_id, event_type, payload, organization_id, occurred_at, published_at), `EventEnvelope` (the on-wire shape consumed by subscribers), `ProjectionCheckpoint` (per-consumer stream position for resume).

**Database Tables Owned.** `outbox` (drained by the relay; rows retained for the event archive window), `event_archive` (durable replay source — projections rebuildable from here in minutes), `projection_checkpoints`.

**Services.** `OutboxWriter` (a transactional helper every domain service uses — appends the outbox row inside the caller's tx), `RelayWorker` (drains `outbox` → Redis Streams; idempotent publish keyed on outbox id), `ProjectionDispatcher` (routes events to projection builders and enqueues async jobs), `FreshnessTracker` (tracks relay lag → emits freshness state for honest-degradation badges).

**Controllers.** None client-facing. (Powers all SSE invalidation and projection freshness.)

**Events Published.** Transports *all* events; publishes its own `relay.lag_exceeded` ops signal.

**Events Consumed.** Every domain event (as transport), to fan out to projections/jobs.

**Queue Workers.** `outbox-relay-worker` (drain → publish), `projection-builder-worker` (per projection family), `event-archive-worker` (durable archive write).

**AI Agents Used.** None.

**External Integrations.** Redis (Streams) Year-1; Kafka/MSK seam Year-2.

**Dependencies.** M-03 (Core Schema), Redis provisioned.

**Security Responsibilities.** Events carry `organization_id`; consumers must filter on it (code-review-gated, same rule as `BYPASSRLS` workers). No event payload may leak cross-tenant data into a shared stream consumer.

**Complexity.** Medium.

**Recommended Build Order.** **3** (every projection and async pipeline rides it).

**Forward-compatibility seam.** Outbox is the invariant; swapping Redis Streams → Kafka/MSK at cell scale is non-breaking behind the relay (TD-2).

---

## M-05 · BFF & API Gateway (Three-Protocol Contract)

| | |
|---|---|
| **Feature ID(s)** | F-05 |
| **Group** | Platform Services |

**Purpose.** The single client-facing surface. No client ever calls a domain service, the Model Gateway, Qdrant, or Postgres directly. Implements the three-protocol rule (REST commands + simple reads / GraphQL lenses / SSE streams), the async-job grammar, and all wire invariants (`Claim[]`, one error format, cursor-only pagination, two-key versioning, mandatory `Idempotency-Key`). Ships with a built-in dev fixture implementing the exact `/api/v1` contract so frontend and backend parallelize.

**Domain Models.** `IdempotencyRecord` (`(org, user, method, path, key)` → cached response + body_hash, 24h TTL), `JobResource` (`{id, status, progress:{pct,stage,detail}, result_ref|error}`), `ApiVersion` (URI `/v1` + `PMOS-Version` date header), `ErrorEnvelope` (the one error shape), `StreamTicket` ownership shared with M-02.

**Database Tables Owned.** `idempotency_keys` (response cache, 24h), `jobs` (the async-job registry backing the universal grammar). GraphQL persisted-query registry (`persisted_queries`).

**Services.** `BffRouter` (JWKS verify → TenantContext → idempotency → rate-limit → error-envelope → route), `RestCommandGateway`, `GraphQLGateway` (persisted-queries-only in prod, complexity budget cost ≤1000 / depth ≤8 evaluated pre-execution, DataLoader batching mandatory, complexity counts against rate limit), `SSEGateway` (uniform event envelope, `Last-Event-ID` resume, 15s heartbeats, 15-min server termination), `JobGrammarService` (`202 + job` for every slow op; `/jobs/{id}`, `/jobs/{id}/stream`, `/jobs/{id}/cancel`), `IdempotencyMiddleware`, `RateLimiter` (per-class: read/command/graphql/ask/run/ingest).

**Controllers.** `POST /api/graphql`; `GET /api/v1/jobs/{jobId}`; `GET /api/v1/jobs/{jobId}/stream` (SSE); `POST /api/v1/jobs/{jobId}/cancel`. (Plus the cross-cutting middleware that fronts *every* endpoint in every module.)

**Events Published.** `job.created`, `job.progressed`, `job.succeeded`, `job.failed`, `job.cancelled`.

**Events Consumed.** Domain completion events that resolve jobs (e.g. `run.completed` → job `succeeded`).

**Queue Workers.** None of its own (it dispatches into other modules' queues); `job-reaper-worker` (expire stale jobs, GC idempotency cache).

**AI Agents Used.** None directly (carries `Claim[]` payloads).

**External Integrations.** None directly (fronts all internal services).

**Dependencies.** M-01, M-03.

**Security Responsibilities.** JWKS-verify every JWT; resolve+enforce TenantContext before any data touch (`403 tenant_context_unresolved`); enforce idempotency (financial/ceremonial keys bound into hash-chain at the owning module); rate-limit per class; never let a client param set `organization_id`/`workspace_id`; emit the one error envelope that never leaks cross-tenant data.

**Complexity.** High.

**Recommended Build Order.** **5** (gates every client surface; ship the dev fixture early so frontend/backend parallelize).

**Forward-compatibility seam.** `/v1` + `PMOS-Version` + persisted queries + capability tokens are the same machinery the Year-3 public Platform/API (F-56) uses; no re-architecture.

---

## M-07 · Model Gateway

| | |
|---|---|
| **Feature ID(s)** | F-07 |
| **Group** | Platform Services |

**Purpose.** Single gateway fronting all frontier-model and embedding calls, with ZDR contracts and no cross-tenant training by default. Per-task tiered routing (frontier / mid / small / embedding), hot-swappable routing policy, batch lanes for autonomous work, and provider failover that degrades Ask to a lower tier with a *visible quality badge* (honest degradation).

**Domain Models.** `ModelRoute` (task_class → tier → provider/model binding), `RoutingPolicy` (hot-swappable per task type), `ProviderHealth` (per-provider availability/quota), `DegradationState` (current tier + quality badge), `BatchLane` vs `InteractiveLane` (priority classes).

**Database Tables Owned.** `model_routes`, `routing_policies`, `provider_health` (or Redis-backed for hot state). Token-cost facts are written to `agent_runs`/`agent_run_steps` (owned by M-06), not here.

**Services.** `ModelGateway` (the call surface every AI module uses), `Router` (tiered task→model routing; default agent model `claude-sonnet-4-6` per `ai_agents.model`; embeddings `text-embedding-3-large` @ 3072 dims), `FailoverController` (provider outage → lower tier + badge; quota → batch-lane queueing), `LaneScheduler` (interactive prioritized over batch), `ZDRGuard` (enforces ZDR contracts, blocks cross-tenant training).

**Controllers.** None client-facing except degradation visibility: contributes `GET /api/v1/ops/status` (with M-42) and inline `503 provider_degraded` + quality badge on Ask/run streams.

**Events Published.** `model.provider_degraded`, `model.failover_engaged`, `model.routing_policy_updated`, `embedding.model_changed` (triggers blue/green collection swap in M-12).

**Events Consumed.** `ops.killswitch_engaged` (per-tenant/agent/tool/level halt).

**Queue Workers.** `batch-lane-worker` (drains autonomous-work model calls), `provider-health-probe-worker`.

**AI Agents Used.** None (it *is* the model access layer all agents call).

**External Integrations.** Anthropic Claude, OpenAI embeddings (both under ZDR). Pluggable additional providers behind the router.

**Dependencies.** M-06 (model binding lives in `ai_agents`).

**Security Responsibilities.** ZDR enforcement; no cross-tenant training by default; never log raw tenant content to provider-side retention; honest degradation (visible badge, never silent quality drop).

**Complexity.** Medium.

**Recommended Build Order.** **4** (gates every AI feature; built alongside M-06).

**Forward-compatibility seam.** Gateway + tiered routing is the architecture; Sonnet is the configured default; routing policy hot-swappable per task type (TD-9). BYOK model custody lands with cells (Year-2).

---

## M-21 · Audit Fabric (Append-Only Hash-Chained Substrate)

| | |
|---|---|
| **Feature ID(s)** | F-21 |
| **Group** | Platform Services |

**Purpose.** One audit pattern for every auditable surface (Decision Ledger, autonomy/approval log, billing meters): append-only Postgres tables where each row `row_hash = H(prev_hash ‖ canonical_payload)`, hourly chain-head anchored to WORM S3, full OTel trace linkage, `UPDATE`/`DELETE` revoked at the role level. This is the same machinery sold as "insurance-grade audit" in Year-3.

**Domain Models.** `AuditChain` (per-surface chain head + anchor history), `HashChainedRow` (the append-only row contract: prev_hash, canonical_payload, row_hash), `ChainAnchor` (hourly WORM S3 anchor: chain_head_hash, anchored_at), `VerificationResult`.

**Database Tables Owned.** The chaining substrate + `audit_chain_heads`, `chain_anchors`. Provides the **append-only chaining primitive** used by `decision_ledger` (M-28), the approval/autonomy log (M-18), and `billing_meters` (M-34) — those tables live in their modules but use this module's canonical hashing/anchoring service.

**Services.** `HashChainAppender` (computes `row_hash`, enforces append-only inside the caller's tx), `ChainAnchorWorker`-facing `AnchorService` (hourly head → WORM S3), `AuditVerifier` (re-derives the chain, detects tampering), `OTelLinker` (binds every audit row to its trace).

**Controllers.** `GET /api/v1/audit/verify` (chain integrity check; mismatch = sev-1 integrity alert).

**Events Published.** `audit.chain_anchored`, `audit.integrity_alert` (sev-1 on chain-head mismatch).

**Events Consumed.** None (other modules call its append service synchronously inside their tx).

**Queue Workers.** `chain-anchor-worker` (hourly WORM anchor), `chain-verify-worker` (periodic + on-demand verification).

**AI Agents Used.** None.

**External Integrations.** S3/WORM (immutable anchors).

**Dependencies.** M-03, S3/WORM storage.

**Security Responsibilities.** Threat #5 (audit-record tampering). `UPDATE`/`DELETE` revoked at role level; chain-head anchored to WORM hourly; chain-bound idempotency keys so a replayed ceremonial write can never double-append; tamper-evidence is the inspected surface for security reviews.

**Complexity.** Medium.

**Recommended Build Order.** **6** (gates the ledger, autonomy log, and billing meters).

---

## M-34 · Consumption Metering & Billing Meters

| | |
|---|---|
| **Feature ID(s)** | F-34 |
| **Group** | Platform Services |

**Purpose.** Per-autonomy-unit metering built on `agent_runs` token accounting, written to append-only billing meters via the audit fabric. Exposes a predictable platform fee and metered autonomy units side by side so the first cohort chooses the mix.

**Domain Models.** `BillingMeter` (append-only metered unit: run_id, task_class, token_cost, autonomy_unit, organization_id, occurred_at), `UsageRollup` (per-tenant/period aggregation projection), `PlanBinding` (platform_fee | consumption | hybrid).

**Database Tables Owned.** `billing_meters` (append-only, hash-chained via M-21), `usage_rollups` (rebuildable projection).

**Services.** `MeteringService` (reads `agent_runs` token meters → writes billing meters), `UsageRollupProjector` (period aggregation), `PlanResolver` (which units bill under which plan).

**Controllers.** `GET /api/v1/billing/meters`; GraphQL **usage lens** (via M-05 GraphQL gateway).

**Events Published.** `billing.meter_recorded`, `billing.rollup_updated`.

**Events Consumed.** `run.completed` / `run.failed` (token cost finalization), `model.*` (cost attribution).

**Queue Workers.** `usage-rollup-worker` (period aggregation, idempotent so token cost is never double-counted).

**AI Agents Used.** None (reads token meters).

**External Integrations.** Downstream billing/invoicing system (export only; PMOS never stores payment instruments).

**Dependencies.** M-06 (token accounting), M-21 (append-only meters).

**Security Responsibilities.** Append-only, hash-chained meters; idempotent rollups (run dispatch dedupe so cost is never double-counted); meter rows carry `organization_id`; no cross-tenant aggregation.

**Complexity.** Medium.

**Recommended Build Order.** Rides on M-06 + M-21; build alongside the agents (Wave 3), not after them.

---

## M-41 · Compliance Baseline & GDPR Erasure Cascade

| | |
|---|---|
| **Feature ID(s)** | F-41 |
| **Group** | Platform Services |

**Purpose.** SOC 2 Type II posture and the GDPR DSAR/erasure cascade ≤24h via tombstones that leave typed "redacted" stubs, so ledger auditability survives content removal. (ISO 27001 and BYOK are Year-2.)

**Domain Models.** `DSARRequest` (subject, scope, status), `Tombstone` (redaction marker over a soft-deleted/purged row), `RedactedStub` (typed placeholder preserving graph/ledger structure), `ComplianceControl` (SOC 2 control evidence handle).

**Database Tables Owned.** `dsar_requests`, `tombstones`, `compliance_controls`. Operates *over* every content table's soft-delete path (`deleted_at`) but does not own those tables.

**Services.** `DSARService` (orchestrates the erasure cascade as an async job), `ErasureCascadeWorker`-facing `CascadeService` (walks the graph, leaves redacted stubs, preserves hash-chain), `ComplianceEvidenceCollector` (SOC 2 control evidence).

**Controllers.** `POST /api/v1/dsar` (async-job grammar: `202 + job`).

**Events Published.** `dsar.requested`, `dsar.completed`, `content.redacted`.

**Events Consumed.** `organization.*` (residency context), soft-delete events from content modules.

**Queue Workers.** `dsar-erasure-worker` (the cascade; ≤24h SLA), `compliance-evidence-worker`.

**AI Agents Used.** None.

**External Integrations.** SOC 2 auditor evidence export; WORM S3 (erasure must not break audit anchors).

**Dependencies.** M-01, M-02, M-21, M-03 (soft-delete path).

**Security Responsibilities.** Threat #4 (insider/over-privileged access) posture; GDPR erasure with audit survival (redacted stubs preserve ledger auditability); erasure cascade ≤24h; never erase the hash-chain itself (only chained content payloads → redacted stub).

**Complexity.** Medium.

**Recommended Build Order.** Cross-cutting; build in parallel once M-01/M-02/M-21 exist (Wave 4). Gates deals — do not leave to the end.

---

## M-42 · Resilience & Kill Switches

| | |
|---|---|
| **Feature ID(s)** | F-42 |
| **Group** | Platform Services |

**Purpose.** In-cell multi-AZ HA (RTO 1h / RPO ≤5 min), model-provider failover degrading Ask to a lower tier with a visible quality badge, and kill switches per tenant / agent / tool / autonomy-level — the operator's emergency stop and a prerequisite for granting any autonomy.

**Domain Models.** `KillSwitch` (scope: tenant | agent | tool | level; state), `DegradationBadge` (current quality tier surfaced to clients), `HealthStatus` (per-subsystem), `RecoveryObjective` (RTO/RPO targets).

**Database Tables Owned.** `kill_switches`, `ops_status` (or Redis-backed hot state).

**Services.** `KillSwitchService` (engage/release per scope; takes effect immediately at the policy engine + tool service + Model Gateway), `OpsStatusAggregator` (composes degradation/health from M-07, M-17, M-18), `FailoverCoordinator` (works with M-07's failover controller).

**Controllers.** `POST /api/v1/ops/killswitch`; `GET /api/v1/ops/status`.

**Events Published.** `ops.killswitch_engaged`, `ops.killswitch_released`, `ops.degraded`, `ops.recovered`.

**Events Consumed.** `model.provider_degraded`, `run.failed` (health signal), `audit.integrity_alert`.

**Queue Workers.** `health-probe-worker`.

**AI Agents Used.** None.

**External Integrations.** AWS multi-AZ; PagerDuty/ops alerting.

**Dependencies.** M-07, M-17, M-18.

**Security Responsibilities.** Per-level kill switch is the prerequisite for granting any autonomy; engaging a kill switch must instantly stop token issuance (M-18) and tool execution (M-19). Honest degradation (visible badge, never silent staleness — principle 8).

**Complexity.** Medium.

**Recommended Build Order.** Cross-cutting; build in parallel with the agent runtime (Wave 4) so a misbehaving agent can always be halted.


---

# Group 2 — Identity Services

*Who the human is, what they may see, and the human-curated containers (Streams) plus shareable views (Lenses). Authority is a session claim; passwords never touch PMOS.*

---

## M-02 · Identity & Access (Clerk Integration)

| | |
|---|---|
| **Feature ID(s)** | F-02 |
| **Group** | Identity Services |

**Purpose.** Clerk as canonical identity provider: SSO (SAML/OIDC), MFA for editor+ roles, SCIM (enterprise), JWKS-verified JWTs. Passwords never touch PMOS endpoints. Resolves a human's authority as a session claim (RBAC × ABAC × source-ACL trim) and mints SSE stream tickets.

**Domain Models.** `User` (display_name, email, `auth_provider` *as an identity-source label only*, never a credential store), `Session` (resolved authority: roles[], abac{stream_ids, max_sensitivity}, mfa_satisfied), `IdentityBinding` (org↔IdP via Clerk: idp, metadata_url, scim_enabled), `StreamTicket` (single-use 60s, audience-scoped `(user, org, stream_kind)`), `Role` (`viewer | editor | admin | owner`), `ABACAttrs`.

**Database Tables Owned.** `users` (projection of Clerk identities + PMOS-side authority labels), `identity_bindings`, `stream_tickets` (single-use). No password column exists anywhere (TD-7).

**Services.** `IdentityService` (Clerk JWKS verification, session resolution), `AuthorityResolver` (RBAC × ABAC × source-ACL trim computed per request), `StreamTicketMinter` (single-use 60s tickets, burned on first use), `SCIMProvisioner` (enterprise), `StepUpMFAService` (re-auth claim for sensitive ops like identity-binding writes).

**Controllers.** `GET /api/v1/session`; `POST /api/v1/session/logout`; `POST /api/v1/stream-tickets`; `PUT /api/v1/organizations/{orgId}/identity-binding`; `GET /api/v1/organizations/{orgId}/identity-binding`; `GET/PATCH /api/v1/users/me`; `GET /api/v1/users/{id}`; `GET /api/v1/workspaces/{wsId}/users`.

**Events Published.** `user.provisioned`, `user.deprovisioned` (SCIM), `identity_binding.updated`, `session.logout`.

**Events Consumed.** `organization.created` (seed first admin), `workspace.created`.

**Queue Workers.** `scim-sync-worker` (enterprise provisioning reconciliation).

**AI Agents Used.** None.

**External Integrations.** Clerk (SSO SAML/OIDC, MFA, SCIM, JWKS). PMOS never holds credentials.

**Dependencies.** F-01 (tenant context binds to identity); F-06 for user projections.

**Security Responsibilities.** Threat #3 (source-credential theft) class eliminated — passwords never touch PMOS. Human authority = session claim; MFA enforced for editor+; step-up MFA for identity-binding writes; SSE auth uses single-use stream tickets, never the session JWT in a URL. Source-ACL trim is computed here and applied pre-fusion in retrieval (M-13).

**Complexity.** Medium.

**Recommended Build Order.** **2** (binds to tenant context; gates all human authority).

**Forward-compatibility seam.** `auth_provider` is retained only as an identity-source label populated from Clerk (TD-7).

---

## M-36 · Streams, Lenses & Membership

| | |
|---|---|
| **Feature ID(s)** | F-36 |
| **Group** | Identity Services |

**Purpose.** The container model. **Stream** = the only human-curated container (a durable area of responsibility); **Lens** = a saved, shareable canvas configuration; **Brief** = a generated ephemeral narrative (owned by M-32, never stored stale). Workspace = one company = one graph. Manages Streams, Lenses, and workspace membership/roles. There is deliberately no classic "teams" table — team-like grouping is membership + ABAC over Streams.

**Domain Models.** `Workspace` (one company = one graph; owned jointly with M-03 for the row but membership lives here), `Stream` (durable area of responsibility; the soft ABAC boundary unit), `StreamMembership` (user↔stream + role), `Lens` (saved canvas configuration; shareable), `WorkspaceMembership` (user↔workspace + role).

**Database Tables Owned.** `streams`, `stream_memberships`, `lenses`, `lens_shares`, `workspace_memberships`.

**Services.** `StreamService` (CRUD, membership), `LensService` (save/share canvas configs), `MembershipService` (workspace + stream roles; feeds ABAC narrowing in M-02's AuthorityResolver).

**Controllers.** `POST/GET/PATCH/DELETE /api/v1/streams[/{id}]`; `POST/DELETE /api/v1/streams/{id}/members[/{userId}]`; `POST/GET /api/v1/lenses[/{id}]`; `POST /api/v1/lenses/{id}/share`; `GET /api/v1/workspaces/{wsId}/members`; `PATCH /api/v1/workspaces/{wsId}/members/{userId}` (role change).

**Events Published.** `stream.created`, `stream.updated`, `stream.archived`, `stream.member_added`, `stream.member_removed`, `lens.created`, `lens.shared`, `membership.role_changed`.

**Events Consumed.** `workspace.created` (seed default Stream), `user.deprovisioned` (cascade membership removal).

**Queue Workers.** None of note (membership changes are interactive writes).

**AI Agents Used.** None.

**External Integrations.** None.

**Dependencies.** F-03 (core schema), F-35 (canvas — Lens is a saved canvas config).

**Security Responsibilities.** Stream is the *soft* ABAC boundary (join-based, deliberately distinct from the hard org RLS boundary); membership drives ABAC narrowing (`stream_ids`, `max_sensitivity`) in every read. Lens shares respect ABAC — a shared lens never widens what a recipient may see.

**Complexity.** Medium.

**Recommended Build Order.** Wave 4 (V1 surface); needs M-03 + M-35.


---

# Group 3 — Knowledge Platform

*The system of record plus the strictly-sequential ingestion→retrieval pipeline — the longest build pole. Each stage consumes the previous stage's typed output: Connectors → Screening → Extraction → Entity Resolution → Index Fan-Out → Retrieval. "Ingested content is hostile until proven otherwise"; "the graph is the system, retrieval is a view of it."*

---

## M-03 · Core Persistence & Product Hierarchy

| | |
|---|---|
| **Feature ID(s)** | F-03 |
| **Group** | Knowledge Platform |

**Purpose.** PostgreSQL 16 as the system of record. The explicit, first-class product hierarchy (`Organization → Workspace → {Products → Features → Epics → User Stories → Requirements}`) plus Roadmaps/Releases, Feedback/Interviews/Insights, and Documents (immutable versions + chunks). PMs and agents both reason in these terms — not a generic work-item table.

**Domain Models.** `Workspace`, `Product`, `Feature`, `Epic`, `UserStory`, `Requirement` (the strict hierarchy), `Document` + `DocumentVersion` (immutable) + `DocumentChunk` (the Postgres↔Qdrant contract), `Account` (entity-resolution target), `Interview`, `Insight`, plus polymorphic edge models `Comment`, `Tag`, `Link`, `Conversation` via `(context_type, context_id)`.

**Database Tables Owned.** `workspaces` (row), `products`, `features`, `epics`, `user_stories`, `requirements`, `roadmaps`, `releases`, `feedback`, `interviews`, `insights`, `documents`, `document_versions`, `document_chunks`, `accounts`, and the polymorphic `comments`, `tags`, `links`, `conversations`. All carry `organization_id` + `workspace_id`, UUIDv7 PKs, `timestamptz`, `updated_at` trigger, `deleted_at` soft delete.

**Services.** `HierarchyService` (CRUD across products→requirements with referential integrity), `DocumentService` (immutable versioning + chunking), `ChunkContract` (maintains `document_chunks.id = Qdrant point id`, `content_hash` for dedupe), `WorkspaceService` (workspace row lifecycle; membership in M-36).

**Controllers.** `POST/GET/PATCH/DELETE /api/v1/{products|features|epics|user-stories|requirements}[/{id}]`; `POST/GET /api/v1/documents[/{id}]`; `GET /api/v1/documents/{id}/versions[/{versionId}]`; `POST/GET/PATCH /api/v1/workspaces[/{id}]`.

**Events Published.** `product.*`, `feature.*`, `epic.*`, `user_story.*`, `requirement.*`, `document.created`, `document.version_created`, `document.chunked`, `workspace.created`.

**Events Consumed.** `prd.approved` / `story.approved` (create immutable doc versions to index), entity-resolution upserts from M-11.

**Queue Workers.** `chunker-worker` (document → chunks on version create), `chunk-reconciler-worker` (nightly Postgres↔Qdrant drift heal — coordinated with M-12).

**AI Agents Used.** None directly (schema is AI-aware by design via M-06).

**External Integrations.** Supabase Storage (user-facing blobs), S3 (archives).

**Dependencies.** F-01.

**Security Responsibilities.** Every row carries `organization_id` + `workspace_id` (RLS + shard key + Qdrant filter source). Soft-delete is the GDPR erasure path (30-day trash → purge). Document chunks carry source `read_principals` (written at upsert by M-11) for pre-fusion ACL trim.

**Complexity.** High.

**Recommended Build Order.** **1** (with M-01; bedrock — every content row depends on it).

**Forward-compatibility seam.** Generic bitemporal typed graph (F-55) layers *over* these tables (not a migration) when the polymorphic-to-strict-FK ratio crosses ~1:4 (TD-6).

---

## M-08 · Connectors & Ingestion Ingress

| | |
|---|---|
| **Feature ID(s)** | F-08 |
| **Group** | Knowledge Platform |

**Purpose.** A connector SDK plus ≥6 source connectors (Zendesk/Jira + Notion/Confluence as the value-proof minimum; Slack/Linear; Gong/Salesforce as honestly-surfaced coverage *upgrades*). Connectors expose webhooks/CDC and source ACL data for ≤2-min freshness and ≤1h ACL reconciliation. The ingress that feeds the strictly-sequential pipeline on three priority lanes.

**Domain Models.** `Connector` (provider, auth handle ref, health, coverage), `SourceCredential` (a *reference* into the secret store — never the credential itself, never in PMOS tables), `RawContentEnvelope` (normalized ingress payload), `SourceACL` (`read_principals` captured at ingress), `BackfillJob`, `WebhookEvent` (deduped on `external_event_id`).

**Database Tables Owned.** `connectors`, `connector_health`, `connector_coverage`, `ingestion_events` (raw envelopes pre-screening, lane-tagged), `webhook_dedupe` (`external_event_id` idempotency).

**Services.** `ConnectorSDK` (the framework new connectors implement), per-provider connector adapters, `OAuthCallbackHandler`, `WebhookReceiver` (HMAC-verified, deduped), `LaneRouter` (live ≤2 min / standard ≤15 min / bulk best-effort), `CoverageEstimator` ("+ Gong would raise customer-voice coverage 41% → 78%"), `ACLCapturer`.

**Controllers.** `POST/GET/DELETE /api/v1/connectors[/{id}]`; `GET /api/v1/connectors/{id}/health`; `GET /api/v1/connectors/{id}/coverage`; `POST /api/v1/connectors/{id}/backfill`; `POST /api/v1/connectors/oauth/callback`; `POST /api/v1/webhooks/connectors/{provider}`.

**Events Published.** `connector.authorized`, `connector.health_changed`, `content.ingested` (per envelope, lane-tagged), `backfill.enqueued`, `connector.deauthorized`.

**Events Consumed.** `ops.killswitch_engaged` (halt a connector/tenant).

**Queue Workers.** `backfill-worker` (bulk lane), `cdc-poller-worker` (for sources without webhooks), `acl-reconciler-worker` (≤1h ACL drift heal).

**AI Agents Used.** None directly (feeds the enrichment pipeline).

**External Integrations.** Zendesk, Jira, Notion, Confluence, Slack, Linear (Year-1 six); Gong, Salesforce (coverage upgrades). Secret store (e.g. AWS Secrets Manager / Vault) for credentials.

**Dependencies.** F-03, F-04.

**Security Responsibilities.** Threat #3 (credential theft): credentials live only in the secret store, never in PMOS tables, never returned by any API. Webhooks HMAC-verified + deduped (an unverified webhook reaching the pipeline is a security failure — Critical API). Source `read_principals` captured at ingress for downstream ACL trim. All ingested content is data, never instructions (handed to M-09 for screening before anything else touches it).

**Complexity.** High.

**Recommended Build Order.** Start in **parallel with Wave 0** (needs only F-03/F-04 stubs; itself a long pole — six connectors). Pipeline stage 1.

**Forward-compatibility seam.** Coverage framed as upgrade drives expansion without lengthening the sales cycle (Q1 default: tickets+docs sufficient).

---

## M-09 · Ingestion Screening (PII & Prompt-Injection)

| | |
|---|---|
| **Feature ID(s)** | F-09 |
| **Group** | Knowledge Platform |

**Purpose.** The Knowledge-plane front half's safety gate: normalization → **PII screening + prompt-injection screening** (`quarantine:injection_suspect`, rendered inert) → ready for enrichment. First layer of injection defense-in-depth. Backfills never delay live detection (lane discipline).

**Domain Models.** `ScreeningResult` (pii_findings, injection_score, verdict), `QuarantineItem` (inert, awaiting manual release), `NormalizedContent` (clean, typed, lane-tagged), `PIIRedaction`.

**Database Tables Owned.** `screening_results`, `quarantine` (injection_suspect items, rendered inert), `normalized_content` (ready-for-extraction).

**Services.** `Normalizer` (per-source-type normalization), `PIIScreener` (detection + redaction), `InjectionScreener` (prompt-injection classification → quarantine), `QuarantineService` (inert storage + manual release), `LaneScheduler` (live protected over standard/bulk).

**Controllers.** `GET /api/v1/ingestion/quarantine`; `POST /api/v1/ingestion/quarantine/{itemId}/release`.

**Events Published.** `content.normalized`, `content.quarantined`, `content.released`, `pii.detected`.

**Events Consumed.** `content.ingested` (from M-08, lane-tagged).

**Queue Workers.** `screening-worker` (per-lane: live/standard/bulk), `pii-screening-worker`, `injection-screening-worker`.

**AI Agents Used.** Pipeline models via the Model Gateway (small-tier classifiers, not the eleven named agents).

**External Integrations.** Model Gateway (M-07) for classifier inference.

**Dependencies.** F-08, F-04.

**Security Responsibilities.** Threat #2 (prompt injection — the AI-native attack), layer 1 of defense-in-depth: screen at ingestion, quarantine suspects, render inert. PII detection/redaction. False-negative (injection slips) is caught downstream by structural separation + tool-schema arg rejection (M-19) — defense-in-depth, not single-point.

**Complexity.** High.

**Recommended Build Order.** Pipeline stage 2 (after M-08).

---

## M-10 · Signal Extraction & Enrichment (the "crown jewel")

| | |
|---|---|
| **Feature ID(s)** | F-10 |
| **Group** | Knowledge Platform |

**Purpose.** Turn normalized content into typed objects — the crown jewel that converts undifferentiated text into the structured graph everything downstream reasons over. A ticket → typed `FeedbackAtom`s, a call → `Commitment`s, a Slack thread → `DecisionCandidate`s, plus `RiskSignal`s. Cheap-first cascade so ~70% of records never touch an LLM.

**Domain Models.** `FeedbackAtom` (typed feedback unit), `Commitment` (customer-facing promise — feeds M-50/Promise Radar in V2), `DecisionCandidate` (mined decision — feeds M-28/Archivist), `RiskSignal`, `ExtractionTrace` (which cascade tier handled each record, metered).

**Database Tables Owned.** `feedback_atoms`, `commitments`, `decision_candidates`, `risk_signals`, `extraction_traces`.

**Services.** `ExtractionOrchestrator` (cheap-first cascade: rules/small-model → mid → frontier only when needed), `TypedAtomExtractor` (per output type), `CascadeRouter` (cost-tiering so ~70% never touch an LLM), `ExtractionMetering` (every LLM-touch metered to `agent_runs`).

**Controllers.** None client-facing (internal pipeline; observable via `GET /api/v1/runs/{id}/steps`).

**Events Published.** `feedback_atom.extracted`, `commitment.extracted`, `decision_candidate.mined`, `risk_signal.raised`.

**Events Consumed.** `content.normalized` (from M-09).

**Queue Workers.** `extraction-worker` (per-lane), `cascade-tier-worker` (escalation to higher model tiers).

**AI Agents Used.** Pipeline extraction models via Model Gateway (cheap-first cascade); not the eleven named agents, but metered to `agent_runs` like them.

**External Integrations.** Model Gateway (M-07).

**Dependencies.** F-09.

**Security Responsibilities.** Operates only on screened/normalized content (post-M-09). Extraction output carries provenance forward to M-11. Source content enters models only inside delimited typed evidence blocks (structural separation — injection defense layer 2).

**Complexity.** High.

**Recommended Build Order.** Pipeline stage 3 (after M-09).

---

## M-11 · Entity Resolution & Graph Upsert

| | |
|---|---|
| **Feature ID(s)** | F-11 |
| **Group** | Knowledge Platform |

**Purpose.** Resolve extracted atoms to canonical entities (e.g. a `FeedbackAtom` → an `Account`) and upsert into the product hierarchy *with provenance*. Source-ACL `read_principals` carried onto every chunk/node at upsert time — the load-bearing moment that makes every later read both citable and permission-safe.

**Domain Models.** `EntityMatch` (atom → canonical entity, confidence), `Provenance` (source_id, chunk_id, evidence_weight), `ReadPrincipals` (the source-ACL carried at upsert), `GraphUpsert` (the write into hierarchy + link tables).

**Database Tables Owned.** `entity_matches`, `provenance` (the resolvable provenance handles), `provenance_links` (typed edges atom↔entity). Writes `read_principals` onto `document_chunks` and hierarchy/`accounts` rows (owned by M-03) at upsert.

**Services.** `EntityResolver` (record linkage / dedupe to canonical entities), `GraphUpsertService` (writes resolved entities + provenance into the hierarchy), `ProvenanceWriter` (every node/chunk gets a resolvable provenance handle + `read_principals`), `AmbiguityQueue` (low-confidence matches for review).

**Controllers.** None client-facing (internal pipeline; provenance read later via M-14).

**Events Published.** `entity.resolved`, `graph.upserted`, `provenance.written`, `account.linked`.

**Events Consumed.** `feedback_atom.extracted`, `commitment.extracted`, `decision_candidate.mined`, `risk_signal.raised` (from M-10).

**Queue Workers.** `entity-resolution-worker`, `graph-upsert-worker`, `ambiguity-review-worker`.

**AI Agents Used.** Pipeline ER models via Model Gateway (embedding/similarity for linkage).

**External Integrations.** Model Gateway (M-07) for embedding-based matching.

**Dependencies.** F-10, F-03.

**Security Responsibilities.** Writes `read_principals` at upsert — the source of pre-fusion ACL trim everywhere downstream. PMOS never widens access beyond what the source granted. ACL drift reconciled ≤1h (coordinated with M-08's reconciler).

**Complexity.** High.

**Recommended Build Order.** Pipeline stage 4 (after M-10).

---

## M-12 · Vector & Lexical Index Fan-Out (Qdrant)

| | |
|---|---|
| **Feature ID(s)** | F-12 |
| **Group** | Knowledge Platform |

**Purpose.** Index fan-out from Postgres to Qdrant. Vectors are **derived and always rebuildable** — nothing in Qdrant that can't be regenerated. `document_chunks` is the Postgres↔Qdrant contract (chunk `id` = Qdrant point id; `content_hash` prevents redundant re-embedding; nightly reconciler heals drift). Payload-partitioned single-collection-per-granularity, `is_tenant`-indexed `organization_id` force-injected; native sparse+dense hybrid (no separate lexical engine Year-1); int8 quantization; collections dimension-fixed (blue/green on model change).

**Domain Models.** `IndexPoint` (Qdrant point: id = chunk id, dense+sparse vectors, payload incl. `organization_id` + `read_principals`), `EmbeddingJob`, `ReconcilerReport` (drift heal), `Collection` (dimension-fixed, blue/green).

**Database Tables Owned.** `embedding_jobs`, `index_reconciler_state`. The vectors themselves live in **Qdrant** (derived; reachable only from the retrieval service M-13). Postgres `document_chunks` (M-03) is the source of truth.

**Services.** `IndexFanOut` (chunk → embed → upsert Qdrant point), `Embedder` (OpenAI `text-embedding-3-large` @ 3072d via Model Gateway; `content_hash` dedupe), `Quantizer` (int8), `BlueGreenCollectionManager` (dimension change = new collection swap), `ChunkReconciler` (nightly Postgres↔Qdrant drift heal).

**Controllers.** None client-facing (queried only via `GET /api/v1/search` and Ask retrieval, both through M-13).

**Events Published.** `chunk.indexed`, `index.reconciled`, `collection.swapped`.

**Events Consumed.** `document.chunked`, `graph.upserted` (from M-03/M-11), `embedding.model_changed` (from M-07 → blue/green swap).

**Queue Workers.** `embedding-worker` (batch lane; `content_hash` dedupe), `index-reconciler-worker` (nightly drift heal).

**AI Agents Used.** Embedding model via Model Gateway (M-07).

**External Integrations.** Qdrant (vectors), Model Gateway (M-07) for embeddings.

**Dependencies.** F-11, F-07 (embeddings), Qdrant provisioned.

**Security Responsibilities.** `organization_id` is `is_tenant`-indexed in the Qdrant payload and **force-injected from context, never request params** (enforcement layer #2 — mirrors RLS). Qdrant reachable only from the retrieval service. Index points carry `read_principals` for pre-fusion ACL trim.

**Complexity.** High.

**Recommended Build Order.** Pipeline stage 5 (after M-11; needs M-07).

**Forward-compatibility seam.** OpenSearch BM25 leg only if exact-match recall drops below ~0.95 on the largest tenant (TD-8); the `GET /search` contract is engine-agnostic. Per-tenant Qdrant collections at cell scale (Year-2, fail-closed).

---

## M-13 · Hybrid GraphRAG Retrieval

| | |
|---|---|
| **Feature ID(s)** | F-13 |
| **Group** | Knowledge Platform |

**Purpose.** The Retrieval plane: parallel vector + lexical + typed-graph traversal + governed metric-store tool calls + ledger lookups → fusion → rerank → **ACL-trimmed select (pre-fusion)** → claim-grounded generation → groundedness verification. Tiered search (coarse pre-filter → rescore → cross-encoder → LLM-select). **Numbers are tools, not text.** Honest abstention ("n sources withheld by permissions"). The sole Qdrant path.

**Domain Models.** `RetrievalQuery`, `Candidate` (pre-fusion, ACL-trimmed), `FusionResult`, `RerankResult`, `GroundedAnswer` (`Claim[]`), `AbstentionResult` (withheld-source count + reason).

**Database Tables Owned.** None of its own persistence (reads `document_chunks` source-of-truth, Qdrant vectors, ledger, metric store). May own a `retrieval_cache` (Redis-backed) for hot queries.

**Services.** `RetrievalOrchestrator` (parallel legs → fusion → rerank → ACL-trim → select), `HybridFuser` (dense + sparse), `Reranker` (cross-encoder; per-tenant heads in V2), `GraphTraverser` (typed-graph leg over hierarchy + provenance links), `ACLTrimmer` (pre-fusion, using `read_principals` × session authority from M-02), `GroundednessVerifier`, `AbstentionController` (honest "n sources withheld").

**Controllers.** `GET /api/v1/search` (Go <50ms; no generative LLM in the hot path). Backs the Ask retrieval path (M-29) and the canvas/Brief lenses (M-35/M-32).

**Events Published.** `search.executed` (metered), `abstention.recorded` (feeds the ≥95% honesty metric in M-20).

**Events Consumed.** None directly (synchronous read path).

**Queue Workers.** None (interactive read path; reranker-head training is V2).

**AI Agents Used.** Pipeline rerank/LLM-select models via Model Gateway; consumed *by* the named agents as a tool (`GET /search`).

**External Integrations.** Qdrant (via M-12), Model Gateway (M-07), metric store (M-15), ledger (M-28).

**Dependencies.** F-12, F-11, F-07, F-15 (metric store for numeric tool calls).

**Security Responsibilities.** Pre-fusion ACL trim everywhere (enforcement layer #3 at read time): a result the asker can't see in the source is removed before fusion and the omission surfaced honestly, never silently dropped. `organization_id` force-injected into the Qdrant filter. Groundedness verification clears P4's veto bar.

**Complexity.** High.

**Recommended Build Order.** Pipeline stage 6 (after M-12; the end of the long pole). Gates every cited surface and all generative agents.

---

## M-14 · Claim[] Protocol & Provenance Substrate

| | |
|---|---|
| **Feature ID(s)** | F-14 |
| **Group** | Knowledge Platform |

**Purpose.** The protocol that makes "every generated sentence a contract." Every AI-generated prose field anywhere is a `Claim[]` (`{text, citations[], kind, confidence}`) at the wire level. Uncitable claims marked `inference` and rendered differently. Provenance resolvable in <400ms. Redundant non-color encoding (the Provenance Underline) for accessibility.

**Domain Models.** `Claim` (`text, citations[], kind: fact|inference|simulated, confidence`), `Citation` (`source_id, chunk_id?, uri?, evidence_weight: single|corroborated|inference|simulated|degraded`), `ProvenanceHandle` (resolvable to source + chunk), `ProvenanceChain` (the evidence lineage).

**Database Tables Owned.** None new — resolves over `provenance` + `provenance_links` (M-11) and `document_chunks` (M-03). `Claim[]` is a wire/serialization contract enforced at M-05, not a table.

**Services.** `ClaimSerializer` (the wire type enforced on every AI-prose response), `ProvenanceResolver` (`source_id`/`chunk_id` → source + deep link in <400ms; chunk id = Qdrant point id, no mapping table), `ClaimTyper` (fact vs inference vs simulated), `ConfidenceCalibrator`.

**Controllers.** `GET /api/v1/provenance/{id}`; `GET /api/v1/provenance/{id}/chain`.

**Events Published.** None of note (read/resolution path).

**Events Consumed.** `provenance.written` (warms resolver caches).

**Queue Workers.** None (interactive resolution path).

**AI Agents Used.** None directly (every agent *emits* `Claim[]`; this module defines and resolves the type).

**External Integrations.** None (resolves internal provenance).

**Dependencies.** F-13 (generation produces Claims), F-05 (`Claim[]` is a wire invariant).

**Security Responsibilities.** Provenance resolution respects ACL — a citation the asker can't see is handled by abstention upstream (M-13), not leaked here. `simulated` claims rendered violet ("anything not yet real is violet").

**Complexity.** Medium.

**Recommended Build Order.** Pipeline stage 6b (with M-13; generation produces Claims, provenance resolves them).

---

## M-15 · Governed Metric Store

| | |
|---|---|
| **Feature ID(s)** | F-15 |
| **Group** | Knowledge Platform |

**Purpose.** The governed store of quantitative facts that AI features call as tools — the **only legitimate origin for any number in any generated claim** ("numbers are tools, not text"). Inspectable join logic for outcome-attribution credibility with a CFO.

**Domain Models.** `Metric` (definition, dimensions, governance metadata), `MetricQuery` (`metric_id, dims, window`), `MetricResult` (value + provenance + inspectable join logic), `MetricCatalogEntry`.

**Database Tables Owned.** `metrics` (definitions + governance), `metric_facts` (the governed quantitative values), `metric_query_cache` (Redis-backed by `(metric_id, dims, window)`).

**Services.** `MetricStoreService` (the tool surface every number-emitting generation calls), `MetricResolver` (query → governed value + provenance), `MetricCatalog` (discoverable metric definitions), `JoinLogicInspector` (CFO-grade transparency), `MetricGovernance` (who may define/expose metrics).

**Controllers.** `POST /api/v1/metrics/query`; `GET /api/v1/metrics/catalog`. (Outcome scorecards `GET /outcomes/...` are V2/M-48 seam.)

**Events Published.** `metric.queried` (metered + provenance), `metric.defined`.

**Events Consumed.** Ingestion/analytics events that update metric facts.

**Queue Workers.** `metric-rollup-worker` (precompute hot metric windows).

**AI Agents Used.** None (it is the tool agents call for numbers).

**External Integrations.** Source analytics/instrumentation systems (read; PMOS does not replace instrumentation engines — out of scope per spec).

**Dependencies.** F-03.

**Security Responsibilities.** A fabricated number destroys CFO trust — the governed origin is non-negotiable. `metric_unavailable` → "unmeasurable as predicted," never invented. Metric facts carry `organization_id`; no cross-tenant metric joins. Inspectable join logic for outcome credibility.

**Complexity.** Medium.

**Recommended Build Order.** Wave 1 (alongside the pipeline; gates F-13 numeric tool calls and all number-emitting generation).

---

## M-16 · Memory Plane (Four Cognitive Types × Three Scopes)

| | |
|---|---|
| **Feature ID(s)** | F-16 |
| **Group** | Knowledge Platform |

**Purpose.** The compounding moat. Four cognitive memory types — **working** (task workspace), **episodic** (Decision Ledger + run logs, permanent), **semantic** (the graph/indexes), **procedural** (templates, scoring models, anti-patterns, human-governed) — across three scopes: organizational, product, user. Each agent carries a memory lens.

**Domain Models.** `WorkingMemory` (per-run task workspace), `EpisodicMemory` (read view over ledger + run logs), `SemanticMemory` (read view over graph/indexes), `ProceduralMemory` (templates, scoring models, anti-patterns — human-governed), `MemoryLens` (per-agent scoped view: type × scope).

**Database Tables Owned.** `procedural_memory` (templates, scoring models, anti-patterns; human-governed, versioned), `memory_lenses` (per-agent configuration), `working_memory` (run-scoped task workspaces; ephemeral/checkpointed). Episodic = read view over `decision_ledger` (M-28) + `agent_runs` (M-06); semantic = read view over M-03/M-12.

**Services.** `MemoryPlaneService` (assembles an agent's memory lens), `ProceduralMemoryService` (CRUD on human-governed templates/anti-patterns), `WorkingMemoryStore` (per-run, checkpointed), `MemoryScopeResolver` (org/product/user scoping).

**Controllers.** `GET/PATCH /api/v1/admin/procedural-memory/{kind}[/{id}]` (admin-only; human-governed). Working memory observable via `/runs/{id}/steps` (M-06).

**Events Published.** `procedural_memory.updated`, `anti_pattern.recorded`.

**Events Consumed.** `outcome.necropsy_completed` (V2 → anti-pattern memory), `run.completed` (episodic accrual).

**Queue Workers.** `working-memory-gc-worker` (post-run cleanup), `memory-consolidation-worker` (Archivist hook, V2).

**AI Agents Used.** Feeds all agents (each carries a memory lens); Archivist (V2) is the steward.

**External Integrations.** None.

**Dependencies.** F-03, F-12, F-06.

**Security Responsibilities.** Memory scoped by org/product/user — never cross-tenant. Procedural memory is human-governed (admin role); agents read but do not silently rewrite anti-pattern memory. Anti-pattern memory is per-tenant (the moat; no cross-tenant content sharing — cross-tenant priors are DP-only, Year-3 F-60).

**Complexity.** High.

**Recommended Build Order.** Wave 2 (needs M-03/M-12/M-06; feeds all agents).


---

# Group 4 — AI Platform

*The agent execution substrate. The audit/cost spine, stateless replayable runs, the autonomy contract (capability tokens), the tool enforcement point, the release-gating eval harness, and the always-on Conductor + Research agents. "Autonomy is enforced in the runtime, never in the prompt."*

---

## M-06 · AI Schema Spine (Agents, Runs, Conversations, Metering)

| | |
|---|---|
| **Feature ID(s)** | F-06 |
| **Group** | AI Platform |

**Purpose.** AI as first-class in the schema — the audit & cost spine every agent and metering feature reads from: `ai_agents` (with `model` binding), `conversations`, `messages` (with `tool_calls`), `agent_runs` (token-metered) + `agent_run_steps` (step trace).

**Domain Models.** `AiAgent` (role charter, tool manifest, memory lens, autonomy matrix ref, `model` binding, eval suite, KPIs — a versioned software unit), `Conversation`, `Message` (with `tool_calls`), `AgentRun` (token-metered, the canonical job), `AgentRunStep` (step trace for replay/audit).

**Database Tables Owned.** `ai_agents`, `conversations`, `messages`, `agent_runs` (token-metered), `agent_run_steps` (step trace). `agent_runs`/`agent_run_steps` are the audit/cost spine; metering (M-34) and replay (M-17) both read them.

**Services.** `AgentRegistry` (versioned agent definitions + model bindings), `RunRecorder` (token accounting per run/step), `ConversationService`, `StepTracer` (the replayable step log).

**Controllers.** `GET /api/v1/agents[/{id}]`; `GET /api/v1/runs/{id}/steps`; `GET /api/v1/conversations/{id}`. (Run lifecycle `POST /runs` etc. is owned by M-17.)

**Events Published.** `agent.registered`, `agent.version_updated`, `run.step_recorded`.

**Events Consumed.** `run.started`, `run.completed`, `run.failed` (from M-17, to finalize token accounting).

**Queue Workers.** None of its own (the runtime M-17 drives runs; this records them).

**AI Agents Used.** Defines all eleven agents (this is their schema); does not itself execute.

**External Integrations.** None directly (Model Gateway M-07 binds via `ai_agents.model`).

**Dependencies.** F-03.

**Security Responsibilities.** `agent_runs`/`agent_run_steps` are the audit spine — every agent action is attributable and replayable. Token metering is the basis for consumption billing and the insurance-grade audit. Rows carry `organization_id`.

**Complexity.** Medium.

**Recommended Build Order.** **4** (with M-07; gates every AI feature and all metering).

---

## M-17 · Agent Runtime (Stateless, Checkpointed, Replayable Runs)

| | |
|---|---|
| **Feature ID(s)** | F-17 |
| **Group** | AI Platform |

**Purpose.** The orchestration substrate: a NestJS orchestrator + BullMQ executing stateless, checkpointed, replayable, capability-gated runs. All state lives in the Task Workspace and memory plane; agents are stateless between tasks, so every run is replayable and auditable. Runs kept stateless/checkpointed so the eventual Temporal swap is mechanical. The universal run lifecycle every agent dispatches through.

**Domain Models.** `Run` (the canonical agent job: agent, task_class, inputs, checkpoints, status), `RunCheckpoint` (replay point), `RunStep` (delegates recording to M-06), `TaskWorkspace` (working memory for the run), `ParentChildRun` (Conductor parent + specialist children).

**Database Tables Owned.** `run_checkpoints`, `run_dispatch_dedupe` (so token cost is never double-counted). Reuses `agent_runs`/`agent_run_steps` (M-06) as the run/step records.

**Services.** `AgentOrchestrator` (NestJS; plans tool-use loops, checkpoints, assembles output), `RunDispatcher` (BullMQ enqueue; idempotent dispatch dedupe), `CheckpointService` (stateless replay points), `ReplayService` (re-execute from checkpoint for audit), `LaneAwareScheduler` (interactive vs batch lanes via M-07).

**Controllers.** `POST /api/v1/runs` (`202 + job` whose id = `agent_runs.id`); `GET /api/v1/runs[/{id}]`; `GET /api/v1/runs/{id}/stream` (SSE: `step_started|step_completed|progress|run_completed|run_failed`); `POST /api/v1/runs/{id}/cancel`; `POST /api/v1/runs/{id}/replay`.

**Events Published.** `run.started`, `run.step_completed`, `run.completed`, `run.failed`, `run.cancelled`, `run.replayed`.

**Events Consumed.** `ops.killswitch_engaged` (halt runs per scope), `approval.granted` (resume an L2-gated run).

**Queue Workers.** `run-executor-worker` (BullMQ; interactive + batch lanes), `run-checkpoint-worker`, `run-reaper-worker` (timeouts).

**AI Agents Used.** Executes all eleven agents through one lifecycle.

**External Integrations.** Model Gateway (M-07) at runtime; governed tool service (M-19) for any tool call.

**Dependencies.** F-06, F-07, F-04.

**Security Responsibilities.** Capability-gated runs: an agent cannot call a write tool without a token (verified at M-19). Stateless/checkpointed runs are the audit guarantee (replayability sold to enterprises). Idempotent dispatch so token cost is never double-counted. Kill switches halt runs immediately.

**Complexity.** High.

**Recommended Build Order.** Wave 2 (needs M-06/M-07/M-04). The substrate for all agents.

**Forward-compatibility seam.** Stateless, checkpointed, replayable runs make the Temporal swap mechanical (TD-4); `POST /runs` + the job grammar unchanged. Temporal adopted at ≥3-mutation sagas or contractual replay (Q7, Year-2).

---

## M-18 · Policy Engine & Capability Tokens (L0–L2)

| | |
|---|---|
| **Feature ID(s)** | F-18 |
| **Group** | AI Platform |

**Purpose.** The autonomy contract enforced in the runtime. Authority is a consumable, audited capability token bound to `(run_id, task_class, approval_event, ttl=5min)`, issued by the policy engine, verified cryptographically at the tool service. An agent whose run lacks a token cannot exercise the capability. Year-1 ships L0–L2 token machinery; L2 requires a human approval endpoint; agent writes carry `ai_generated=true` + `source_run_id`.

**Domain Models.** `CapabilityToken` (`run_id, task_class, approval_event, ttl, signature`), `AutonomyMatrix` (per-(agent, task-class) level L0–L4; Year-1 caps at L2), `ApprovalEvent` (the human gate that issues an L2 token), `AutonomyLevel` (L0 observe → L1 draft → L2 act-with-approval → [L3/L4 V2]).

**Database Tables Owned.** `capability_tokens` (issued/consumed, short-lived), `autonomy_matrix` (per-agent/task-class levels), `approval_events` (append-only via M-21, hash-chained), `autonomy_log` (append-only autonomy decisions).

**Services.** `PolicyEngine` (decides whether a token may issue given level + approval), `CapabilityTokenIssuer` (cryptographically signed, 5-min TTL, single action class), `ApprovalService` (the L2 human gate → issues the token), `AutonomyMatrixService` (level config; promotion is a signed ledger decision in V2).

**Controllers.** `POST /api/v1/approvals/{runId}` (the single L2 human gate; issues every capability token; raises `capability_denied`/`approval_required`).

**Events Published.** `approval.granted`, `approval.denied`, `token.issued`, `token.consumed`, `autonomy.level_changed`.

**Events Consumed.** `run.step_completed` (a run reaching a write step requests a token), `ops.killswitch_engaged` (stop token issuance for a scope).

**Queue Workers.** `token-expiry-worker` (5-min TTL enforcement).

**AI Agents Used.** Governs all eleven agents' authority (issues no tokens to L0/L1; L2 requires human approval).

**External Integrations.** None (cryptographic verification is internal; tokens verified at M-19).

**Dependencies.** F-17, F-02.

**Security Responsibilities.** Threat #2 structural guarantee: an L1 agent cannot call a write tool because no token exists in its run (CI attack-success-rate target 0 at the tool-call layer). Approval events are append-only + hash-chained (M-21). Year-1 caps issuance at L0–L2; agent writes carry `ai_generated=true` + `source_run_id`.

**Complexity.** High.

**Recommended Build Order.** Wave 2 (needs M-17/M-02). The token machinery ships in Year-1 specifically so the trust-measurement clock starts early.

**Forward-compatibility seam.** Full L3/L4 token TTL + two-person-rule layer on at Trust Ladder GA (Year-2) **without changing the `POST /approvals` surface** (TD-3).

---

## M-19 · Governed Tool Service

| | |
|---|---|
| **Feature ID(s)** | F-19 |
| **Group** | AI Platform |

**Purpose.** The service that executes agent tool calls, verifying capability tokens cryptographically and rejecting evidence-sourced arguments for sensitive parameters (injection defense-in-depth layer). Tool schemas are the contract; the CI red-team targets a tool-call attack-success-rate of 0. The point where an unauthorized action would actually fire — and is prevented.

**Domain Models.** `ToolManifest` (per-agent allowed tools + schemas), `ToolCall` (validated args, token), `ToolResult`, `SensitiveParameterRule` (rejects evidence-sourced args), `ToolExecution` (audited).

**Database Tables Owned.** `tool_manifests`, `tool_executions` (audited; append-only via M-21 for sensitive tools).

**Services.** `GovernedToolService` (verify token → validate schema → reject evidence-sourced sensitive args → execute), `TokenVerifier` (cryptographic), `SchemaValidator` (tool schemas as the contract), `SensitiveArgGuard` (structural separation: source content never supplies sensitive parameters), `ExternalWriteExecutor` (executes Living Sync pushes etc.).

**Controllers.** None client-facing (internal; executes external writes for `POST /api/v1/sync/push` via M-31).

**Events Published.** `tool.executed`, `tool.denied`, `tool.injection_blocked` (security signal).

**Events Consumed.** `token.issued` (a run holds a token to call a tool), `ops.killswitch_engaged` (halt tool execution).

**Queue Workers.** `tool-execution-worker` (for async/external-write tools).

**AI Agents Used.** Executes tools on behalf of all agents (only under a valid token).

**External Integrations.** Jira/Linear/ADO (external writes via Living Sync), and any source system a tool writes to.

**Dependencies.** F-18, F-17.

**Security Responsibilities.** Threat #2 at the point of action: cryptographic token verification + tool-schema arg rejection for evidence-sourced sensitive parameters + capability confinement (CI attack-success-rate target 0). The enforcement point that makes graduated autonomy safe to sell.

**Complexity.** Medium.

**Recommended Build Order.** Wave 2 (needs M-18/M-17).

---

## M-20 · Eval Harness (Release-Gating)

| | |
|---|---|
| **Feature ID(s)** | F-20 |
| **Group** | AI Platform |

**Purpose.** The evaluation harness that gates releases — explicitly **"built second, not last."** Measures normalized edit-distance on accepted stories, time-to-approval per (team, task-type), groundedness, and the honesty/abstention metric (≥95% correct abstention on unanswerable sets). Encodes the kill/pivot trigger (>30% edit-distance after two quarters). The instrument that converts engineering leads from veto to advocate.

**Domain Models.** `EvalSuite` (per-agent/task-class test sets + gold standards), `EvalRun` (a release-gating evaluation), `EditDistanceMetric` (normalized, per (team, task-type)), `GroundednessScore`, `AbstentionScore`, `QualityGate` (pass/block decision), `KillPivotTrigger` (>30% edit-distance after two quarters).

**Database Tables Owned.** `eval_suites`, `eval_runs`, `eval_results` (edit-distance, groundedness, abstention, approval latency), `gold_standards` (outcome-validated best work; shared with M-52 PM Craft in V2).

**Services.** `EvalHarness` (runs suites; release-gating verdict), `EditDistanceScorer` (normalized; on accepted stories), `GroundednessScorer`, `AbstentionScorer` (the ≥95% honesty metric), `QualityGateService` (blocks sub-bar drafts and sub-bar releases), `TriggerMonitor` (kill/pivot at >30%).

**Controllers.** GraphQL **quality/edit-distance lens** (via M-05). Release-gate runs in CI (not a client endpoint) but feeds the lens.

**Events Published.** `eval.run_completed`, `quality.gate_blocked`, `kill_pivot.triggered`, `edit_distance.recorded`.

**Events Consumed.** `prd.approved` / `story.approved` (edit-distance on accepted artifacts), `abstention.recorded` (from M-13), `run.completed` (groundedness sampling).

**Queue Workers.** `eval-run-worker` (CI-triggered + scheduled).

**AI Agents Used.** Evaluates all generative agents (M-22/24/25/27/39/40); itself uses judge models via Model Gateway.

**External Integrations.** CI system (release gate), Model Gateway (M-07) for LLM-judge evals.

**Dependencies.** F-17, F-13. (Must precede broad agent rollout.)

**Security Responsibilities.** Encodes the P4 engineering quality veto as a release-blocking gate. Edit-distance is a Trust-Ladder promotion gate (feeds M-18/M-44). Abstention scoring enforces honest degradation.

**Complexity.** High.

**Recommended Build Order.** Wave 2, **immediately after the agent runtime exists** — "built second, not last." Edit-distance/groundedness gating protects the single biggest adoption risk (P4's bar).

---

## M-26 · Conductor Agent (AI Chief of Staff)

| | |
|---|---|
| **Feature ID(s)** | F-26 |
| **Group** | AI Platform |

**Purpose.** System agent S1: intake, planning, delegation to specialists, assembly of results, and submission to humans. The orchestration brain that makes the specialist agents usable as a team rather than a toolbox — the control plane behind "every day starts at review and decide."

**Domain Models.** `ConductorPlan` (decomposition of a request into child tasks), `Delegation` (parent run → specialist child runs), `ReviewPackage` (assembled output submitted to a human), `IntakeRequest`.

**Database Tables Owned.** None new (operates over `agent_runs` parent/child via M-06/M-17; plans live in working memory M-16).

**Services.** `ConductorAgent` (intake → plan → delegate → assemble → submit), `Planner` (task decomposition), `Delegator` (dispatches child `POST /runs` per specialist with scoped tokens), `Assembler` (composes a human review package).

**Controllers.** `POST /api/v1/runs (conductor)` (via M-17's run lifecycle).

**Events Published.** `conductor.plan_created`, `conductor.delegated`, `conductor.review_package_ready`.

**Events Consumed.** Child `run.completed`/`run.failed` (to assemble), `tide_item` (V2: Overnight PM triage).

**Queue Workers.** Runs on the agent runtime's executor (parent + child runs).

**AI Agents Used.** S1 Conductor itself; orchestrates A2 Research, A5 PRD, A6 Story, A3 Roadmap, A4 Prioritization (the Year-1 active set).

**External Integrations.** Model Gateway (M-07); child writes gated by `POST /approvals/{runId}` (M-18).

**Dependencies.** F-17, F-18, F-16.

**Security Responsibilities.** Parent/child token scoping — a child run only receives the capability tokens its task-class earned; the Conductor cannot escalate a child's authority. Submits drafts *to* humans (elevate-the-human; the AI's work is always a submitted draft).

**Complexity.** High.

**Recommended Build Order.** Wave 3 (orchestrates the MVP loop; needs M-17/M-18/M-16).

---

## M-27 · Research Agent

| | |
|---|---|
| **Feature ID(s)** | F-27 |
| **Group** | AI Platform |

**Purpose.** Specialist A2: evidence work — feedback synthesis, interviews, market context. The agent face of Feedback Intelligence; turns raw signal into decision-ready evidence and recovers PM hours. L1 read-only (no approval token needed).

**Domain Models.** `ResearchTask` (synthesize_feedback, etc.), `SynthesisResult` (`Claim[]` with citations + contrarian probe), `EvidenceBundle`.

**Database Tables Owned.** None new (writes synthesis as `Claim[]` outputs; reads feedback clusters via M-22, search via M-13).

**Services.** `ResearchAgent` (synthesis with citations + mandatory contrarian probe), `FeedbackSynthesizer`, `ContrarianProbe` ("evidence against" — structural confirmation-bias defense).

**Controllers.** `POST /api/v1/runs (research, synthesize_feedback)` (via M-17).

**Events Published.** `research.synthesis_completed`.

**Events Consumed.** Dispatched by Conductor (M-26) or directly.

**Queue Workers.** Runs on the agent runtime executor.

**AI Agents Used.** A2 Research; uses `GET /search` (M-13), feedback lens (M-22), `POST /metrics/query` (M-15) as tools.

**External Integrations.** Model Gateway (M-07).

**Dependencies.** F-22, F-13, F-17.

**Security Responsibilities.** L1 read-only — no write token needed; groundedness verification on output; mandatory contrarian probe; numbers from the metric store only.

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (needs M-22/M-13/M-17).


---

# Group 5 — Product Intelligence

*The wedge and the loop-closers. Feedback synthesis (the 30-day ROI proof), the GTM diagnostic, evidence-native artifact generation (PRD + Story, gated by the eval harness), the org-wide brain, and the command interface.*

---

## M-22 · Feedback Intelligence (the wedge)

| | |
|---|---|
| **Feature ID(s)** | F-22 |
| **Group** | Product Intelligence |

**Purpose.** The wedge. Every signal clustered, quantified, tied to accounts/revenue, and threaded to the decisions it should inform. Must prove ≥5–6 hrs/PM/week recovered within 30 days on the lowest-trust connector set (tickets+docs alone).

**Domain Models.** `FeedbackCluster` (grouped atoms + quantification + revenue tie), `ClusterThread` (link cluster → decision it should inform), `Quantification` (counts/revenue from the metric store), `AccountTie`.

**Database Tables Owned.** `feedback_clusters`, `cluster_memberships` (atom↔cluster), `cluster_threads` (cluster↔decision), `cluster_projections` (precomputed lens payloads).

**Services.** `FeedbackIntelligenceService`, `Clusterer` (groups `feedback_atoms` from M-10/M-11), `Quantifier` (counts + revenue via metric store M-15), `Threader` (cluster → decision linkage), `ClusterProjector` (precomputes the GraphQL feedback lens).

**Controllers.** GraphQL **feedback-cluster lens** (via M-05); `GET /api/v1/feedback/clusters/{id}`; `POST /api/v1/feedback/clusters/{id}/thread`.

**Events Published.** `cluster.formed`, `cluster.quantified`, `cluster.threaded`.

**Events Consumed.** `feedback_atom.extracted`, `entity.resolved`, `account.linked` (from M-10/M-11).

**Queue Workers.** `clustering-worker` (incremental re-clustering on new atoms), `quantification-worker`.

**AI Agents Used.** A2 Research (M-27) synthesizes over clusters; clustering itself uses embedding/cluster models via Model Gateway.

**External Integrations.** Model Gateway (M-07); metric store (M-15) for revenue/counts.

**Dependencies.** F-10, F-11, F-13, F-15.

**Security Responsibilities.** Clusters respect ACL — a cluster shown to a user trims atoms whose `read_principals` exclude them (pre-fusion). Revenue/counts from the governed metric store only. Rows carry `organization_id`.

**Complexity.** High.

**Recommended Build Order.** Wave 3 (the wedge; needs the full pipeline F-10→F-13 + metric store).

---

## M-23 · The Free Diagnostic (GTM)

| | |
|---|---|
| **Feature ID(s)** | F-23 |
| **Group** | Product Intelligence |

**Purpose.** A free, self-serve diagnostic run as the GTM wedge — ingests a prospect's accessible sources and surfaces findings ("here's what you're not seeing," with coverage estimate and honest gaps) to prove value before purchase. Runs on the bulk lane (never blocks live tenants); trial-scoped and purged.

**Domain Models.** `DiagnosticRun` (trial-scoped principal, scoped sources), `DiagnosticFinding` (`Claim[]`), `CoverageEstimate` (honest gaps + upgrade framing), `TrialPrincipal` (time-boxed, scoped).

**Database Tables Owned.** `diagnostic_runs`, `diagnostic_findings` (trial-scoped; purged after the trial window).

**Services.** `DiagnosticOrchestrator` (async-job grammar: `202 + job`), `CoverageEstimator` (reuses M-08's estimator), `TrialPrincipalIssuer` (scoped, time-boxed), `DiagnosticPurger` (post-trial cleanup).

**Controllers.** `POST /api/v1/diagnostic` (`202 + job`); `GET /api/v1/diagnostic/{id}`; `GET /api/v1/diagnostic/{id}/stream` (SSE progress).

**Events Published.** `diagnostic.started`, `diagnostic.completed`, `diagnostic.purged`.

**Events Consumed.** Pipeline events scoped to the trial principal.

**Queue Workers.** `diagnostic-worker` (bulk lane only — never blocks live tenants), `diagnostic-purge-worker`.

**AI Agents Used.** A2 Research (M-27) for synthesis; feedback synthesis + coverage estimation.

**External Integrations.** Connectors (M-08) under read-only trial scope; Model Gateway (M-07).

**Dependencies.** F-22, F-08, F-09.

**Security Responsibilities.** The only (near-)public surface — runs under a scoped, time-boxed **trial principal**, not a full tenant. Bulk lane isolation so a prospect's diagnostic never degrades a paying tenant. Trial data purged after the window.

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (the GTM top-of-funnel; needs M-22/M-08/M-09).

---

## M-24 · PRD Agent (Evidence-Native, L1/L2)

| | |
|---|---|
| **Feature ID(s)** | F-24 |
| **Group** | Product Intelligence |

**Purpose.** PRD generation where every sentence traces to sources (`Claim[]`): decision → build-ready evidence-native spec. Mandatory contrarian probe ("evidence against"). L1 draft / L2 act-with-approval; human approval endpoint; `ai_generated=true` + `source_run_id` on writes; edit-distance logged. The answer to P4's "AI slop" veto.

**Domain Models.** `PRDDraft` (`Claim[]` body, contrarian-probe section, source_run_id), `PRD` (approved, immutable version), `ContrarianProbe`, `EditDistanceLog`.

**Database Tables Owned.** `prds` (metadata + approval state); the approved body is an immutable `document_version` (M-03). `prd_edit_distance` log (feeds M-20).

**Services.** `PRDAgent` (claim-grounded generation + mandatory contrarian probe), `PRDDraftService` (queues draft, NOT written as final), `ApprovalIntegration` (L2 gate via M-18 → on approval, version + flag `ai_generated`/`source_run_id`), `EditDistanceLogger`.

**Controllers.** `POST /api/v1/runs (prd, draft_prd)` (via M-17); `POST /api/v1/approvals/{runId}` (M-18 gate); `POST/GET /api/v1/prds[/{id}]`.

**Events Published.** `prd.drafted`, `prd.approved`, `prd.edit_distance_logged`.

**Events Consumed.** `decision.committed` (a committed decision triggers a PRD draft), `approval.granted`.

**Queue Workers.** Runs on the agent runtime executor (AI-heavy; eval-gated).

**AI Agents Used.** A5 PRD; uses `GET /search`, `POST /metrics/query`, provenance as tools.

**External Integrations.** Model Gateway (M-07); eval harness (M-20) blocks sub-bar drafts.

**Dependencies.** F-13, F-14, F-17, F-18, F-16, F-20.

**Security Responsibilities.** L2 write requires a human approval event (which issues the capability token). Agent writes carry `ai_generated=true` + `source_run_id`. Eval gate (M-20) blocks sub-bar drafts. Every sentence is a `Claim`; numbers from the metric store.

**Complexity.** High.

**Recommended Build Order.** Wave 3 (the first artifact agent; needs the full retrieval + runtime + eval stack).

---

## M-25 · Story Agent (Engineer-Grade, L1/L2)

| | |
|---|---|
| **Feature ID(s)** | F-25 |
| **Group** | Product Intelligence |

**Purpose.** Spec → epics/stories/ACs at engineer-grade quality, generated as evidence-native `Claim[]`. L1/L2 in Year-1 (the L3 push to Jira is V2/M-47). Edit-distance and approval latency measured per (team, task-type) — where the kill/pivot trigger (>30% after two quarters) is measured. Converting P4 from veto to advocate is the gating risk for the whole roadmap.

**Domain Models.** `StoryDraft` (epic tree + stories + ACs as `Claim[]`), `Epic`/`UserStory`/`Requirement` (committed on approval — owned by M-03), `ACSet`, `EditDistanceLog` (per team/task-type).

**Database Tables Owned.** None new for the hierarchy (commits `epics`/`user_stories`/`requirements` in M-03); owns `story_drafts` (pre-approval) + `story_edit_distance` log (feeds M-20).

**Services.** `StoryAgent` (engineer-grade epic/story/AC generation), `StoryDraftService` (queue draft), `ApprovalIntegration` (L2 → commit hierarchy), `EditDistanceLogger` (per team/task-type; feeds the kill/pivot trigger).

**Controllers.** `POST /api/v1/runs (story, write_stories)` (via M-17); `POST /api/v1/approvals/{runId}` (M-18); on approval, commits `epics/user-stories/requirements` (M-03).

**Events Published.** `story.drafted`, `story.approved`, `story.edit_distance_logged`.

**Events Consumed.** `prd.approved` (a PRD becomes the input spec), `approval.granted`.

**Queue Workers.** Runs on the agent runtime executor (AI-heavy; eval-gated; gold standards from M-20).

**AI Agents Used.** A6 Story Writing; uses `GET /search`, PRD doc, gold standards as tools. (External Jira push via M-19/M-31 is V2-graduated to L3.)

**External Integrations.** Model Gateway (M-07); eval harness + gold standards (M-20). (Living Sync M-31 for the Year-1 one-way push.)

**Dependencies.** F-24, F-13, F-20.

**Security Responsibilities.** L2 in Year-1 (Priya's bar): drafts approved before commit. Edit-distance kill/pivot trigger is the central quality guardrail. `ai_generated`/`source_run_id` on writes.

**Complexity.** High.

**Recommended Build Order.** Wave 3 (after M-24; the quality-convergence long pole).

---

## M-29 · Ask-the-Brain (Org-Wide Product Brain)

| | |
|---|---|
| **Feature ID(s)** | F-29 |
| **Group** | Product Intelligence |

**Purpose.** Anyone asks "why don't we support SSO on Starter?" and gets decision + evidence + owner + review date — claim-grounded, ACL-trimmed, with honest abstention. Token/claim streaming over SSE; first token <700ms. Free unlimited viewers using Ask is how PMOS spreads org-wide (the ubiquity/expansion engine).

**Domain Models.** `AskQuery`, `AskAnswer` (`Claim[]` streamed), `AbstentionResponse` ("n sources withheld by permissions"), `AskConversation` (over M-06 `conversations`).

**Database Tables Owned.** None new (reads retrieval M-13, ledger M-28; conversation persisted via M-06).

**Services.** `AskService` (claim-grounded QA over retrieval + ledger), `AnswerStreamer` (SSE: `token|claim|citation|abstention|done|error`; first token <700ms), `AbstentionController` (honest ≥95% metric, shared logic with M-13).

**Controllers.** `POST /api/v1/ask` (`202`/stream init); `GET /api/v1/ask/{id}/stream` (SSE, first token <700ms); `GET /api/v1/conversations/{id}` (via M-06).

**Events Published.** `ask.answered`, `ask.abstained` (feeds honesty metric).

**Events Consumed.** None directly (interactive path).

**Queue Workers.** None (interactive; prioritized over batch lanes).

**AI Agents Used.** Uses retrieval (M-13) + generation; not one of the eleven named agents but the same grounded-generation spine.

**External Integrations.** Model Gateway (M-07; failover badge on degradation); metric store (M-15) for numbers.

**Dependencies.** F-13, F-14, F-28.

**Security Responsibilities.** Pre-fusion ACL trim + honest abstention (never leak, never silently omit the fact of omission). Numbers from the metric store. First-token budget met by interactive-lane prioritization; degradation surfaced with a visible quality badge.

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (needs M-13/M-14/M-28).

---

## M-33 · The Line (Command Interface) & Search v1

| | |
|---|---|
| **Feature ID(s)** | F-33 |
| **Group** | Product Intelligence |

**Purpose.** The single command interface (`⌘K`, three blended modes — Go / Ask / Do). Go <50ms (navigation/search); Ask streams claims (→ M-29); Do triggers agent actions (→ M-17). The IA deliberately omits folders/page-trees/global lists — if a user wants to "organize," they ask the Line. 100% pointer-free.

**Domain Models.** `LineQuery` (mode-blended intent), `IntentClassification` (Go | Ask | Do), `GoResult` (search hit), `DoDispatch` (agent action trigger).

**Database Tables Owned.** None new (routes into search M-13, Ask M-29, runs M-17).

**Services.** `LineService` (intent classification → route), `IntentRouter` (Go→`GET /search`, Ask→`POST /ask`, Do→`POST /runs`), `GoSearchAdapter` (Go <50ms; no LLM in the hot path).

**Controllers.** `GET /api/v1/search` (Go mode; owned with M-13 — the Line is the primary caller). Ask/Do route to M-29/M-17 controllers.

**Events Published.** `line.command_executed` (lightweight telemetry).

**Events Consumed.** None directly.

**Queue Workers.** None (interactive).

**AI Agents Used.** Intent classification (Go/Ask/Do routing — a small-tier model); Do dispatches the named agents.

**External Integrations.** Model Gateway (M-07) for intent classification.

**Dependencies.** F-13 (Ask/Go), F-29, F-05.

**Security Responsibilities.** Go search respects ACL trim (via M-13). Do dispatches only actions the user's role permits (RBAC at dispatch; agent then runs under capability tokens). No LLM in the <50ms Go hot path.

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (scaffold the front-end shell against the dev fixture earlier; wire to M-13/M-29 when retrieval is live).


---

# Group 6 — Planning Services

*The decision layer — the system of decision above execution and record. The first-class versioned decision object, the ceremony that fills it organically, and the agents that produce defensible plans and rankings.*

---

## M-28 · Decision Ledger v1

| | |
|---|---|
| **Feature ID(s)** | F-28 |
| **Group** | Planning Services |

**Purpose.** Every decision a first-class, versioned object — options, evidence, assumptions, predicted impact, owner, dissent, review date ("git for product decisions"). v1 is the relational realization; entries are a *byproduct of actions people already take* (no standalone "log a decision" form). Hash-chained via the audit fabric. "Decisions are the product; documents are exhaust."

**Domain Models.** `Decision` (versioned: question, options, the call, owner, review_date), `DecisionVersion`, `Assumption` (with verification status), `Guard` (e.g. "gate at 5% until A3 verifies"), `Dissent`, `PredictedImpact` (from the metric store), `Evidence` (citations).

**Database Tables Owned.** `decision_ledger` (append-only, hash-chained via M-21), `decision_versions`, `assumptions`, `guards`, `dissents`. Reads `metric_facts` (M-15) for predicted_impact.

**Services.** `DecisionLedgerService` (versioned decision CRUD as a byproduct), `LedgerAppender` (hash-chain via M-21), `AssumptionTracker`, `GuardService`, `DecisionSheetProjector` (the GraphQL Decision Sheet lens).

**Controllers.** `GET /api/v1/decisions/{id}`; `GET /api/v1/decisions/{id}/history`; GraphQL **Decision Sheet lens** (via M-05). (The write path — `POST /decisions/commit` — is the Commit Ceremony, M-30.)

**Events Published.** `decision.committed`, `decision.version_created`, `assumption.verified`, `guard.tripped`, `decision.review_due`.

**Events Consumed.** `decision_candidate.mined` (V2 Archivist proposes entries), `outcome.window_closed` (V2 attaches realized impact).

**Queue Workers.** `review-date-worker` (surfaces decisions due for review to the Tide).

**AI Agents Used.** None core (capture is a byproduct); enriched by Archivist (V2, M-46). Predicted-impact numbers from the metric store.

**External Integrations.** None (system of record is Postgres + WORM anchors).

**Dependencies.** F-21, F-03, F-30 (commit ceremony populates it).

**Security Responsibilities.** Append-only, hash-chained, signature-verified (M-21) — the system of record for *decisions*; tamper-evidence (threat #5). Financial-grade idempotency: a replayed commit can never double-append. Rows carry `organization_id`.

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (mutually coupled with M-30; blocked by M-21 hash-chain).

**Forward-compatibility seam.** Full generic bitemporal hash-chained Decision Ledger layered over the relational tables (Year-2, TD-6).

---

## M-30 · Commit Ceremony & Decision Sheet

| | |
|---|---|
| **Feature ID(s)** | F-30 |
| **Group** | Planning Services |

**Purpose.** The ceremonial write surface where a human commits a decision (typed initial → hash-chained ledger entry). The Decision Sheet presents The Question and The Call; supports running a Pre-Mortem (synthetic stakeholders — Year-1 scaffold, full synthesis V2) and adding guards. The action that fills the ledger organically (>40% of committed decisions should originate as a byproduct of an existing action).

**Domain Models.** `CommitCeremony` (typed initial = the judgment; ledger entry = its byproduct), `DecisionSheet` (The Question + The Call), `PreMortemScaffold` (Year-1 structured scaffold; V2 = synthetic-stakeholder synthesis), `GuardInput`.

**Database Tables Owned.** None new (writes into `decision_ledger`/`assumptions`/`guards` owned by M-28). Owns the ceremony orchestration only.

**Services.** `CommitCeremonyService` (BFF → domain service → Postgres tx [state + outbox] + hash-chain append + signature verification), `DecisionSheetService`, `PreMortemScaffoldService` (Year-1 structured scaffold), `GuardCommitService`.

**Controllers.** `POST /api/v1/decisions/commit` (ceremonial, hash-chained, signature-verified); `POST /api/v1/decisions/{id}/premortem`; `POST /api/v1/decisions/{id}/guards`.

**Events Published.** `decision.committed` (the same event M-28 records), `premortem.run`, `guard.added`.

**Events Consumed.** `prd.approved` / Arena ranking commit / roadmap re-sequence (the existing actions that fire the ceremony as a byproduct).

**Queue Workers.** None (interactive ceremonial write).

**AI Agents Used.** A1 Strategist (V2) powers full Pre-Mortem; Year-1 returns a structured scaffold.

**External Integrations.** None.

**Dependencies.** F-28, F-21, F-05.

**Security Responsibilities.** Ceremonial write: hash-chain append + signature verification; the typed initial is the human judgment; idempotency key bound into the hash-chain so a replayed commit cannot double-append. "Ceremony only where it matters."

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (mutually coupled with M-28).

---

## M-39 · Roadmap Agent

| | |
|---|---|
| **Feature ID(s)** | F-39 |
| **Group** | Planning Services |

**Purpose.** Specialist A3: the living plan — sequencing, capacity, dependencies, scenarios. Turns the roadmap into a queryable, evidence-linked artifact rather than a static slide. Scenarios are forced `kind:"simulated"` (violet).

**Domain Models.** `Roadmap`, `RoadmapItem` (evidence-linked), `Sequencing`, `CapacityModel`, `Dependency`, `Scenario` (always `simulated`/violet).

**Database Tables Owned.** `roadmaps` (M-03 owns the table; M-39 owns the agent logic + `roadmap_scenarios`, `roadmap_sequencing`).

**Services.** `RoadmapAgent` (sequencing/scenario reasoning), `Sequencer`, `CapacityPlanner`, `ScenarioGenerator` (forces `simulated`), `DependencyResolver`.

**Controllers.** `POST /api/v1/runs (roadmap, sequence_roadmap)` (via M-17); `GET /api/v1/roadmaps/{id}`; `POST /api/v1/roadmaps/{id}/scenarios`; GraphQL **Horizon lens** (via M-05).

**Events Published.** `roadmap.sequenced`, `scenario.generated`, `roadmap.resequenced`.

**Events Consumed.** `decision.committed` (re-sequence triggers), `commitment.extracted` (V2: promise→roadmap link for M-50).

**Queue Workers.** Runs on the agent runtime executor.

**AI Agents Used.** A3 Roadmap; uses Horizon lens, `POST /metrics/query`, ledger, dependencies as tools.

**External Integrations.** Model Gateway (M-07); re-sequence commits via `POST /decisions/commit` (M-30).

**Dependencies.** F-17, F-13, F-28.

**Security Responsibilities.** Re-sequencing a roadmap is a decision → commits through the ceremony (auditable). Scenarios forced `simulated` (violet) — never presented as fact. Governed metric inputs only.

**Complexity.** Medium.

**Recommended Build Order.** Wave 4 (early specialist; needs M-17/M-13/M-28).

---

## M-40 · Prioritization Agent

| | |
|---|---|
| **Feature ID(s)** | F-40 |
| **Group** | Planning Services |

**Purpose.** Specialist A4: defensible ranking, trade-offs, counterfactuals. Mandatory contrarian probe. Inputs grounded in the metric store, never invented — a direct kill of prioritization theater (problem 4).

**Domain Models.** `RankingRun`, `RankedCandidate` (every input metric-store-cited), `TradeOff`, `Counterfactual`, `ContrarianProbe`.

**Database Tables Owned.** `rankings`, `ranking_inputs` (each cited to the metric store).

**Services.** `PrioritizationAgent` (ranking/scoring + counterfactuals + contrarian probe), `RankingScorer`, `TradeOffAnalyzer`, `ContrarianProbe` (mandatory).

**Controllers.** `POST /api/v1/runs (prioritization, rank_candidates)` (via M-17); GraphQL **Arena lens** (via M-05).

**Events Published.** `ranking.completed`, `ranking.committed`.

**Events Consumed.** Dispatched by Conductor or directly; `metric.queried` results feed inputs.

**Queue Workers.** Runs on the agent runtime executor.

**AI Agents Used.** A4 Prioritization; uses Arena lens, `POST /metrics/query` (governed inputs), `GET /search` as tools.

**External Integrations.** Model Gateway (M-07); ranking commits via `POST /decisions/commit` (M-30).

**Dependencies.** F-13, F-15, F-17.

**Security Responsibilities.** Every input metric-store-cited; `metric_unavailable` → "unmeasurable," never invented. Mandatory contrarian probe (structural confirmation-bias defense). A committed ranking goes through the ceremony (auditable).

**Complexity.** Medium.

**Recommended Build Order.** Wave 4 (early specialist; needs M-13/M-15/M-17).

---

# Group 7 — Release Services

*Coherence with the execution tool (PMOS syncs, never replaces) and continuously-current reporting. Full bidirectional sync, Launch Control, and the outcome loop are V2 seams attached here.*

---

## M-31 · Living Sync v1 (One-Way Spec → Jira/Linear/ADO)

| | |
|---|---|
| **Feature ID(s)** | F-31 |
| **Group** | Release Services |

**Purpose.** One-way sync pushing the spec layer to execution tools with diffs + rationale; the foundation for bidirectional drift detection. PMOS syncs, never replaces, the execution tool (respects the non-goal of not ripping out Jira/Linear/ADO).

**Domain Models.** `SyncPush` (spec → external diff + rationale), `SyncState` (per-object coherence), `Diff`, `Rationale` (`Claim[]`), `ExternalRef` (PMOS object ↔ external ticket).

**Database Tables Owned.** `sync_pushes`, `sync_state` (per-object spec↔ticket coherence), `external_refs`.

**Services.** `LivingSyncService` (compose diff + rationale → push), `DiffGenerator`, `RationaleGenerator` (`Claim[]`), `ExternalWriteDispatcher` (executes via the governed tool service M-19 under an L2-approved token).

**Controllers.** `POST /api/v1/sync/push` (L2-approved external write); `GET /api/v1/sync/{id}`; `GET /api/v1/sync/state`.

**Events Published.** `sync.pushed`, `sync.drift_detected` (seam for V2 bidirectional), `sync.failed`.

**Events Consumed.** `story.approved` / `prd.approved` (produces the artifacts to sync).

**Queue Workers.** `sync-push-worker` (external write, retry/backoff), `sync-state-reconciler-worker`.

**AI Agents Used.** A6 Story Writing (M-25) produces the synced artifacts; diff/rationale generation uses Model Gateway.

**External Integrations.** Jira, Linear, ADO (write, via M-19 governed tool service).

**Dependencies.** F-08, F-19, F-24/F-25 (produces the artifacts to sync).

**Security Responsibilities.** Every external write requires L2 approval before the governed tool service executes (M-18/M-19). Evidence-sourced args rejected for sensitive parameters. A revert handle is added in V2 (L3).

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (one-way; needs M-08/M-19/M-24-25).

**Forward-compatibility seam.** Bidirectional Living Sync + revert handles + Story L3 push (Year-2, M-47).

---

## M-32 · Standing Brief & Notify

| | |
|---|---|
| **Feature ID(s)** | F-32 |
| **Group** | Release Services |

**Purpose.** Continuously-current narrative re-rendered from the ledger (never stored stale), published by local 6am ≥99.5% of days. "The system speaks first" — leads with a finding (what changed, what it means, what's recommended). Every claim provenance-linked. Plus baseline notify. Half of the provable wedge ROI.

**Domain Models.** `Brief` (generated ephemeral narrative; re-rendered, never stored stale), `Finding` (what changed + meaning + recommendation, ranked), `BriefProjection` (precomputed for cold-load <1.5s), `NotificationPreference`.

**Database Tables Owned.** `brief_projections` (rebuildable; carries freshness state), `notification_preferences`. The Brief itself is *not* stored stale — it is re-rendered from `decision_ledger` + projections.

**Services.** `BriefRenderer` (claim-grounded narrative from the ledger; finding ranking), `FindingRanker`, `BriefProjector` (precompute for <1.5s cold load + freshness badge), `NotifyService` (baseline notifications).

**Controllers.** GraphQL **Brief lens** (via M-05; cold Brief <1.5s); `GET/PATCH /api/v1/notifications/preferences`.

**Events Published.** `brief.published`, `notification.sent`.

**Events Consumed.** `decision.committed`, `cluster.formed`, `outcome.window_closed` (V2) — anything that changes the finding set; projection-freshness from M-04.

**Queue Workers.** `brief-publish-worker` (local 6am per tenant; ≥99.5%), `brief-projection-worker` (rebuild on ledger change).

**AI Agents Used.** Claim-grounded narrative generation via Model Gateway; Sentinel (V2) feeds findings.

**External Integrations.** Model Gateway (M-07); email/Slack for notify delivery.

**Dependencies.** F-28, F-14, F-13, F-04 (projections).

**Security Responsibilities.** Brief re-rendered from the ledger, never stored stale (honest degradation — freshness badge on SLO breach). Every claim provenance-linked + ACL-trimmed per reader. Per-reader i18n from one graph.

**Complexity.** Medium.

**Recommended Build Order.** Wave 3 (loop-closer; needs M-28/M-14/M-13/M-04).

**Forward-compatibility seam.** Defense Room (V2, M-53) — hostile-Q&A rehearsal — attaches to the Standing Brief.

---

# Group 8 — Administration Services

*The read/notification surfaces that make the loop usable: the canvas lens projections, calm ranked notifications, and the per-reader i18n/theming support the design system needs.*

---

## M-35 · Meridian Canvas Lens & Altitudes

| | |
|---|---|
| **Feature ID(s)** | F-35 |
| **Group** | Administration Services |

**Purpose.** Backend support for one canvas, many lenses. The Meridian: one horizontal spatial axis (left = evidence/past, right = plans/future; outcomes flow right→left). Three altitudes: Org (30k ft) · Stream (3k ft) · Object (ground). Serves the canvas as GraphQL lens projections sized to keep pan/zoom ≥60fps (client renders graph state; no per-frame API call).

**Domain Models.** `CanvasLens` (a projection of graph state at an altitude), `Altitude` (org | stream | object), `MeridianProjection` (spatially-arranged graph slice), `Waypoint`.

**Database Tables Owned.** `canvas_projections` (precomputed per altitude/stream; rebuildable from events).

**Services.** `CanvasLensService` (composes the Stream Canvas / Org / Object lens), `AltitudeProjector` (per-altitude precomputation), `MeridianArranger` (left→right temporal axis layout data).

**Controllers.** GraphQL **Stream Canvas lens** (via M-05; complexity budget keeps payload pan/zoom-friendly).

**Events Published.** `canvas_projection.updated`.

**Events Consumed.** Domain events that change graph state (hierarchy, decisions, clusters, outcomes) → outbox-driven projection rebuild + SSE invalidation.

**Queue Workers.** `canvas-projection-worker` (rebuild on graph change).

**AI Agents Used.** None directly (renders graph state).

**External Integrations.** None.

**Dependencies.** F-05 (GraphQL lenses), F-03.

**Security Responsibilities.** Canvas projections are ACL-trimmed per reader and ABAC-narrowed by Stream membership. Complexity budget + pagination protect the 60fps budget; no per-frame API call (client renders state).

**Complexity.** High.

**Recommended Build Order.** Wave 4 (V1 surface; can build in parallel from Wave 0 against the dev fixture + `Claim[]` shape).

**Forward-compatibility seam.** Multiplayer collaborative canvas (WebSocket presence plane) is the single named Year-3 case; until then canvas changes flow through REST commands + outbox-driven SSE invalidation. Portfolio mode (V2/F-58) is a peer layer above Streams.

---

## M-37 · The Tide & Meridian Bar

| | |
|---|---|
| **Feature ID(s)** | F-37 |
| **Group** | Administration Services |

**Purpose.** The Tide — calm, ranked notifications that interrupt only for Vermilion (contradiction/risk). The Meridian Bar — bottom strip with waypoints (`⌘1–5`), time scrubber, altitude control. Delivered over SSE. The surface where Sentinel findings (V2) reach the human; ranking ships with MVP signals first.

**Domain Models.** `TideItem` (ranked notification + tier), `TideTier` (Now/…; Vermilion interrupts), `MeridianBarState` (waypoints, scrubber position, altitude), `Acknowledgement`.

**Database Tables Owned.** `tide_items`, `tide_acks`, `tide_rankings`.

**Services.** `TideService` (rank + deliver), `FindingRanker` (calm ranking; interrupt only for Vermilion), `TideStreamer` (SSE: `tide_item|tide_clear|interrupt`), `MeridianBarStateService`.

**Controllers.** `GET /api/v1/tide`; `GET /api/v1/tide/stream` (SSE); `POST /api/v1/tide/{id}/ack`.

**Events Published.** `tide.item_created`, `tide.acked`, `tide.cleared`.

**Events Consumed.** `guard.tripped`, `decision.review_due`, `risk_signal.raised`, `sync.drift_detected`, and (V2) Sentinel `contradiction.detected` → Now-tier interrupt.

**Queue Workers.** `tide-ranking-worker` (incremental re-rank on new signals).

**AI Agents Used.** S2 Sentinel (V2, M-43) is the full engine; Year-1 ranks MVP signals.

**External Integrations.** Model Gateway (M-07) for ranking (small tier).

**Dependencies.** F-05 (SSE); F-43 (Sentinel) for full value (V2) — ranking ships with MVP signals first.

**Security Responsibilities.** Tide items ACL-trimmed per reader; one Tide SSE per session; outbox-driven fan-out. Vermilion interrupts reserved for genuine contradiction/risk (calm authority).

**Complexity.** Medium.

**Recommended Build Order.** Wave 4 (V1 surface; needs M-05 SSE; full value with Sentinel V2).

---

## M-38 · Design System Backend Support

| | |
|---|---|
| **Feature ID(s)** | F-38 |
| **Group** | Administration Services |

**Purpose.** Backend support for the Meridian Design System: per-reader i18n from one graph, the provenance-weight metadata the Provenance Underline renders (thickness + glyph, accessible), and the freshness/quality-badge state that honest degradation requires. (The visual system itself is a frontend track; this module supplies the data contracts it needs.)

**Domain Models.** `EvidenceWeight` (single | corroborated | inference | simulated | degraded — drives the Provenance Underline), `FreshnessState` (per projection, for badges), `LocalizationBundle` (per-reader i18n keys), `QualityBadge` (degradation tier).

**Database Tables Owned.** `localization_bundles` (per-reader i18n). Evidence-weight is carried on `Citation` (M-14); freshness on projections (M-04/M-32); this module owns the i18n + badge data contracts.

**Services.** `LocalizationService` (per-reader rendering from one graph), `EvidenceWeightResolver` (maps citation corroboration → underline weight + glyph), `FreshnessBadgeService`, `QualityBadgeService` (surfaces Model Gateway degradation tier).

**Controllers.** None of its own primary surface — supplies metadata on existing responses (`Claim[]` evidence_weight, projection freshness, degradation badge). May expose `GET /api/v1/i18n/bundle` for per-reader localization.

**Events Published.** None of note.

**Events Consumed.** `model.provider_degraded` (→ quality badge), `relay.lag_exceeded` (→ freshness badge).

**Queue Workers.** `localization-bundle-worker` (rebuild per-locale bundles).

**AI Agents Used.** None (presentation support).

**External Integrations.** Translation/localization pipeline (optional).

**Dependencies.** F-14 (provenance to render), F-35.

**Security Responsibilities.** Redundant non-color encoding (thickness + glyph) for accessibility (WCAG 2.2 AA in both atmospheres). Honest degradation: freshness + quality badges are never suppressed.

**Complexity.** Medium.

**Recommended Build Order.** Wave 4 (V1 surface; the design-system frontend track starts in Wave 1 against the API contract; this backend support follows M-14/M-35).


---

# Cross-Cutting Architecture

The seven maps below are derived from the modules above and the source docs' dependency matrices. They are the views an engineer needs to reason about the system as a whole rather than module-by-module.

---

## 1. Backend Dependency Graph

Arrows read **A → B = "A must exist before B."** Grouped by build wave. F-01/F-02/F-05 (tenancy, identity, BFF) are the implicit root of every chain and are shown only at Wave 0.

```
WAVE 0 — PLATFORM SUBSTRATE (no upstream module deps)
──────────────────────────────────────────────────────
  M-01 Tenancy/RLS ─┬─→ M-03 Core Schema ─┬─→ M-04 Event Backbone
                    │                      ├─→ M-06 AI Schema Spine ──→ M-07 Model Gateway
                    │                      ├─→ M-21 Audit Fabric
                    │                      └─→ M-05 BFF/API Contract
                    └─→ M-02 Identity (Clerk)
                              (dev fixture lets frontend start against M-05 here)

WAVE 1 — INGESTION → RETRIEVAL PIPELINE (longest pole; strictly sequential)
──────────────────────────────────────────────────────
  M-03,M-04 ─→ M-08 Connectors ─→ M-09 Screening ─→ M-10 Extraction ─→ M-11 Entity Resolution
                                                                              │
            M-07 ───────────────────────────────────→ M-12 Index Fan-Out ←───┘
            M-15 Metric Store ──┐                            │
                                └────────────────→ M-13 Hybrid GraphRAG Retrieval
                                                             │
                                          M-14 Claim[]/Provenance ←┘

WAVE 2 — AGENT RUNTIME & AUTONOMY (needs M-06,M-07,M-04,M-02)
──────────────────────────────────────────────────────
  M-17 Agent Runtime ─┬─→ M-18 Policy Engine/Tokens (L0–L2) ─→ M-19 Governed Tool Service
                      └─→ M-20 EVAL HARNESS   ◀── "built second, not last"
  M-16 Memory Plane (needs M-03,M-12,M-06) ──→ feeds all agents

WAVE 3 — MVP LOOP (needs Waves 1–2)
──────────────────────────────────────────────────────
  M-13,M-14,M-17,M-18,M-16,M-20 ─→ M-24 PRD Agent ─→ M-25 Story Agent
  M-13 ─→ M-27 Research Agent ;  M-10,M-11,M-13,M-15 ─→ M-22 Feedback Intelligence ─→ M-23 Diagnostic
  M-21 ─→ M-28 Decision Ledger ⇄ M-30 Commit Ceremony
  M-13,M-14,M-28 ─→ M-29 Ask-the-Brain ;  M-28,M-14,M-13,M-04 ─→ M-32 Standing Brief & Notify
  M-17,M-18,M-16 ─→ M-26 Conductor (orchestrates the loop)
  M-24/M-25,M-19 ─→ M-31 Living Sync v1 (one-way)
  M-13,M-29 ─→ M-33 The Line ;  M-06,M-21 ─→ M-34 Metering

WAVE 4 — V1 SURFACES & EARLY SPECIALISTS (parallel front-end track)
──────────────────────────────────────────────────────
  M-05,M-03 ─→ M-35 Canvas Lens ─→ M-36 Streams/Lenses/Membership ; M-38 Design-System Support
  M-05 (SSE) ─→ M-37 Tide/Meridian Bar
  M-17,M-13,M-28 ─→ M-39 Roadmap Agent ;  M-13,M-15,M-17 ─→ M-40 Prioritization Agent
  M-01,M-02,M-21 ─→ M-41 Compliance ;  M-07,M-17,M-18 ─→ M-42 Resilience/Kill Switches

(V2 seams attach to: M-37→Sentinel, M-18/M-20/M-21→Trust Ladder GA, M-28/M-10→Archivist,
 M-31→bidirectional sync + Story L3, M-15/M-28→Outcome Attribution, M-32→Defense Room.)
```

**The three longest poles (where schedule risk concentrates):**
1. **Ingestion → retrieval (M-08 → M-13).** Strictly sequential, six high-complexity stages; cannot be shortcut. Start M-08 (Connector SDK) in parallel with Wave 0 to claw back time.
2. **Generated-artifact quality (M-24/M-25 gated by M-20).** A quality-convergence problem with a kill/pivot trigger (>30% edit-distance after two quarters), not a code-length problem. M-20 must be mature early so tuning has runway.
3. **Trust/autonomy progression (M-18 → M-20 → [V2 Trust Ladder GA]).** Each rung is earned by *measured* accuracy over time; the calendar is the constraint, which is why M-18 ships in Year-1 to start the clock.

---

## 2. Service Ownership Matrix

Every API surface and its single owning module. The **BFF (M-05)** fronts every endpoint (JWKS verify, TenantContext, idempotency, rate limit, error envelope) and is omitted per row. Datastores: PostgreSQL 16 (system of record), Qdrant (vectors, retrieval-service-only), Redis (cache + Streams + BullMQ), S3/WORM (audit anchors), Supabase Storage (blobs).

| Owning module | Group | APIs / surfaces owned | Primary datastore(s) |
|---|---|---|---|
| **M-01 Tenancy** | Platform | `POST/GET/PATCH /organizations`; force-injection of `organization_id` on **all** reads; workspace stamping | Postgres |
| **M-02 Identity** | Identity | `GET /session`, `POST /session/logout`, `POST /stream-tickets`, `PUT/GET /organizations/{id}/identity-binding`, `GET/PATCH /users/me`, `GET /users/{id}`, `GET /workspaces/{id}/users` | Postgres + Clerk |
| **M-03 Core Schema** | Knowledge | `POST/GET/PATCH/DELETE /products·features·epics·user-stories·requirements`, `POST/GET /documents`, `/documents/{id}/versions`, `POST/GET/PATCH /workspaces` | Postgres + Supabase/S3 |
| **M-04 Event Backbone** | Platform | (no client endpoint) — transport beneath all SSE + projection freshness | Redis Streams + Postgres (outbox/archive) |
| **M-05 BFF/Gateway** | Platform | `POST /api/graphql`, `GET /jobs/{id}{,/stream}`, `POST /jobs/{id}/cancel`; all cross-cutting middleware | Redis (idempotency cache) + Postgres (jobs) |
| **M-06 AI Schema Spine** | AI | `GET /agents{,/{id}}`, `GET /runs/{id}/steps`, `GET /conversations/{id}` | Postgres |
| **M-07 Model Gateway** | Platform | (internal) — `GET /ops/status` degradation; `503 provider_degraded` badge | Redis (hot state) + provider APIs |
| **M-08 Connectors** | Knowledge | `POST/GET/DELETE /connectors`, `/connectors/{id}/{health,coverage,backfill}`, `/connectors/oauth/callback`, `POST /webhooks/connectors/{provider}` | Postgres + secret store |
| **M-09 Screening** | Knowledge | `GET /ingestion/quarantine`, `POST /ingestion/quarantine/{id}/release` | Postgres |
| **M-10 Extraction** | Knowledge | (internal pipeline; metered to `agent_runs`, observable via `/runs/{id}/steps`) | Postgres |
| **M-11 Entity Resolution** | Knowledge | (internal pipeline; writes provenance/`read_principals`) | Postgres |
| **M-12 Index Fan-Out** | Knowledge | (internal; queried only via M-13) | Qdrant + Postgres |
| **M-13 Retrieval** | Knowledge | `GET /search`; backs Ask + canvas/Brief lenses (sole Qdrant path) | Qdrant (via M-12) + Redis cache |
| **M-14 Claim[]/Provenance** | Knowledge | `GET /provenance/{id}`, `/provenance/{id}/chain` | Postgres |
| **M-15 Metric Store** | Knowledge | `POST /metrics/query`, `GET /metrics/catalog` | Postgres + Redis cache |
| **M-16 Memory Plane** | Knowledge | `GET/PATCH /admin/procedural-memory/{kind}` | Postgres |
| **M-17 Agent Runtime** | AI | `POST /runs`, `GET /runs/{id}{,/stream,/steps}`, `POST /runs/{id}/{cancel,replay}` | Postgres + Redis/BullMQ |
| **M-18 Policy Engine** | AI | `POST /approvals/{runId}`; issues capability tokens; `capability_denied`/`approval_required` | Postgres (append-only via M-21) |
| **M-19 Governed Tool Service** | AI | (internal) — executes external writes for `POST /sync/push`; verifies tokens | Postgres + external APIs |
| **M-20 Eval Harness** | AI | GraphQL quality/edit-distance lens; release gate (CI) | Postgres |
| **M-21 Audit Fabric** | Platform | `GET /audit/verify`; hash-chain append inside commit/approval/billing; hourly WORM anchor | Postgres + S3/WORM |
| **M-22 Feedback Intelligence** | Product Intel | GraphQL feedback-cluster lens, `GET /feedback/clusters/{id}`, `POST /feedback/clusters/{id}/thread` | Postgres |
| **M-23 Diagnostic** | Product Intel | `POST /diagnostic`, `GET /diagnostic/{id}{,/stream}` | Postgres (trial-scoped) |
| **M-24 PRD Agent** | Product Intel | `POST /runs (prd)`, `POST/GET /prds` | Postgres |
| **M-25 Story Agent** | Product Intel | `POST /runs (story)`, commits `epics/user-stories/requirements` | Postgres |
| **M-26 Conductor** | AI | `POST /runs (conductor)` | Postgres |
| **M-27 Research Agent** | AI | `POST /runs (research)` | Postgres |
| **M-28 Decision Ledger** | Planning | `GET /decisions/{id}{,/history}`, GraphQL Decision Sheet lens | Postgres (append-only via M-21) + S3/WORM |
| **M-29 Ask-the-Brain** | Product Intel | `POST /ask`, `GET /ask/{id}/stream` | (reads M-13/M-28) |
| **M-30 Commit Ceremony** | Planning | `POST /decisions/commit`, `/decisions/{id}/{premortem,guards}` | Postgres (append-only via M-21) |
| **M-31 Living Sync** | Release | `POST /sync/push`, `GET /sync/{id}`, `/sync/state` | Postgres + Jira/Linear/ADO |
| **M-32 Standing Brief & Notify** | Release | GraphQL Brief lens, `GET/PATCH /notifications/preferences` | Postgres (projections) |
| **M-33 The Line** | Product Intel | `GET /search` (Go; co-owned with M-13), routes Ask/Do | (routes into M-13/M-29/M-17) |
| **M-34 Metering** | Platform | `GET /billing/meters`, GraphQL usage lens | Postgres (append-only via M-21) |
| **M-35 Canvas Lens** | Admin | GraphQL Stream Canvas lens | Postgres (projections) |
| **M-36 Streams/Lenses** | Identity | `/streams{,/members}`, `/lenses{,/share}`, `/workspaces/{id}/members` | Postgres |
| **M-37 Tide/Meridian Bar** | Admin | `GET /tide{,/stream}`, `POST /tide/{id}/ack` | Postgres + Redis (SSE fan-out) |
| **M-38 Design-System Support** | Admin | `GET /i18n/bundle`; metadata on existing responses | Postgres |
| **M-39 Roadmap Agent** | Planning | `POST /runs (roadmap)`, GraphQL Horizon lens, `/roadmaps/{id}/scenarios`, `GET /roadmaps/{id}` | Postgres |
| **M-40 Prioritization Agent** | Planning | `POST /runs (prioritization)`, GraphQL Arena lens | Postgres |
| **M-41 Compliance** | Platform | `POST /dsar` | Postgres + S3/WORM |
| **M-42 Resilience** | Platform | `POST /ops/killswitch`, `GET /ops/status` | Redis (hot state) + Postgres |

**Shared (load-bearing) surfaces** — consumed by many modules; strictest contract stability + most test coverage:
- `POST /runs` + `GET /runs/{id}{,/stream,/steps}` (M-17) — the universal run lifecycle for all agents.
- `POST /approvals/{runId}` (M-18) — the single L2 human gate; issues every capability token.
- `POST /decisions/commit` (M-30) — the commit ceremony that fills the ledger as a byproduct.
- `GET /search` (M-13) — the retrieval substrate under navigation and every grounded generation.
- `POST /metrics/query` (M-15) — the only governed origin of any number.
- `GET /provenance/{id}` (M-14) — resolves every Claim's evidence.
- `POST /api/graphql` (M-05) — the single lens surface.
- `GET /jobs/{id}{,/stream}` + `POST /jobs/{id}/cancel` (M-05) — the one async-job grammar.
- `POST /stream-tickets` (M-02) — the shared SSE-auth primitive.
- `GET /session` (M-02) — the shared authority/TenantContext resolver.

---

## 3. Event Architecture Map

**Invariant (TD-2):** no event without its state change committed in the same Postgres transaction (the transactional outbox, M-04). Transport is Redis Streams Year-1 behind the relay worker; Kafka/MSK seam at cell scale. Every event carries `organization_id`; consumers filter on it (code-review-gated). SSE event envelopes (`token|claim|...`, `step_started|...`, `tide_item|...`) are the *client-facing* projection of these internal events; they are not the events themselves.

**Publisher → event → principal consumers:**

| Publisher (module) | Event | Consumed by |
|---|---|---|
| M-01 | `organization.created` / `.updated` / `.residency_set` | M-02 (seed admin), M-03 (workspace), M-41 |
| M-03 | `workspace.created` | M-02, M-16 (init memory scopes), M-32, M-36 (seed Stream) |
| M-03 | `document.version_created` / `.chunked` | M-12 (index fan-out) |
| M-03 | `product/feature/epic/user_story/requirement.*` | M-35 (canvas projection), M-13 (graph traversal source) |
| M-04 | `relay.lag_exceeded` | M-42, M-38 (freshness badge) |
| M-08 | `content.ingested` (lane-tagged) | M-09 (screening) |
| M-08 | `connector.authorized` / `.health_changed` | M-23, M-42, ops |
| M-09 | `content.normalized` | M-10 (extraction) |
| M-09 | `content.quarantined` / `pii.detected` | security ops, M-37 (admin Tide) |
| M-10 | `feedback_atom.extracted` | M-11, M-22 |
| M-10 | `commitment.extracted` | M-11, (V2 M-50 Promise Radar) |
| M-10 | `decision_candidate.mined` | (V2 M-46 Archivist → M-28) |
| M-10 | `risk_signal.raised` | M-11, M-37 (Tide) |
| M-11 | `entity.resolved` / `graph.upserted` / `account.linked` / `provenance.written` | M-12, M-14 (warm caches), M-22 |
| M-12 | `chunk.indexed` / `index.reconciled` / `collection.swapped` | M-13 (retrieval readiness) |
| M-07 | `model.provider_degraded` / `.failover_engaged` | M-42, M-38 (quality badge) |
| M-07 | `embedding.model_changed` | M-12 (blue/green collection swap) |
| M-17 | `run.started` / `.step_completed` / `.completed` / `.failed` / `.cancelled` | M-06 (token finalize), M-05 (job resolve), M-18 (token request), M-20 (groundedness sample), M-34 (cost) |
| M-18 | `approval.granted` / `.denied`, `token.issued` / `.consumed`, `autonomy.level_changed` | M-17 (resume run), M-19 (tool exec), M-21 (audit) |
| M-19 | `tool.executed` / `.denied` / `.injection_blocked` | M-21 (audit), security ops |
| M-20 | `quality.gate_blocked` / `kill_pivot.triggered` / `edit_distance.recorded` | release pipeline, M-18 (promotion gate), ops |
| M-21 | `audit.chain_anchored` / `audit.integrity_alert` | ops (sev-1 on integrity alert) |
| M-22 | `cluster.formed` / `.quantified` / `.threaded` | M-32 (Brief findings), M-27 (research input) |
| M-24 | `prd.drafted` / `.approved` / `.edit_distance_logged` | M-25 (input spec), M-30 (byproduct commit), M-20, M-31 (sync), M-03 (version) |
| M-25 | `story.drafted` / `.approved` / `.edit_distance_logged` | M-31 (sync), M-20, M-03 (commit hierarchy) |
| M-28 / M-30 | `decision.committed` | M-24 (trigger PRD), M-32 (Brief), M-39 (re-sequence), M-35 (canvas), M-21 (audit) |
| M-28 | `guard.tripped` / `decision.review_due` | M-37 (Tide) |
| M-31 | `sync.pushed` / `sync.drift_detected` / `.failed` | M-37 (Tide drift), (V2 bidirectional sync) |
| M-32 | `brief.published` / `notification.sent` | clients (SSE), notify channels |
| M-37 | `tide.item_created` / `.acked` / `.cleared` | clients (SSE) |
| M-42 | `ops.killswitch_engaged` / `.released` / `ops.degraded` | M-07, M-17, M-18, M-19, M-08 (halt scope) |
| M-41 | `dsar.requested` / `.completed` / `content.redacted` | content modules (cascade), M-21 (preserve chain) |
| M-34 | `billing.meter_recorded` / `.rollup_updated` | billing export |

**Three SSE event families (client-facing projection over the above):**
- **Ask stream** (`/ask/{id}/stream`): `token · claim · citation · abstention · done · error`.
- **Run progress** (`/runs/{id}/stream`, `/jobs/{id}/stream`): `step_started · step_completed · progress · run_completed · run_failed`.
- **The Tide** (`/tide/stream`): `tide_item · tide_clear · interrupt`.

All resumable via `Last-Event-ID`; 15s heartbeats; 15-min server termination + client resume.

---

## 4. Queue Architecture

Year-1 transport is **Redis Streams** (events, via the M-04 relay) + **BullMQ on Redis** (agent runs + background jobs, via M-17). The invariant is the outbox; the queue engine is swappable (Kafka/MSK + Temporal at cell scale). Three **ingestion priority lanes** and a **batch/interactive split** on the Model Gateway are the load-shedding primitives.

**Lane discipline (the load-bearing scheduling rule):**
- **Live lane (≤2 min):** webhook-driven ingestion + live contradiction detection. Never blocked by backfills.
- **Standard lane (≤15 min):** routine CDC/poll ingestion.
- **Bulk lane (best-effort):** backfills, the Diagnostic, re-embedding. Never delays the live lane.
- **Interactive model lane:** Ask first-token, interactive generation — prioritized over batch on the Model Gateway.
- **Batch model lane:** Overnight PM (V2), backfill embedding, bulk extraction — isolated from interactive budgets.

**Worker inventory (by owning module):**

| Queue / worker | Owner | Engine / lane | Responsibility |
|---|---|---|---|
| `outbox-relay-worker` | M-04 | Redis Streams | drain outbox → publish (idempotent on outbox id) |
| `projection-builder-worker` | M-04 | Redis Streams | rebuild read projections from events |
| `event-archive-worker` | M-04 | — | durable archive (projections rebuildable in minutes) |
| `cross-tenant-probe-worker` | M-01 | nightly cron | probe for cross-tenant reads (any hit = sev-0) |
| `backfill-worker` | M-08 | bulk lane | historical source backfill |
| `cdc-poller-worker` | M-08 | standard lane | poll sources without webhooks |
| `acl-reconciler-worker` | M-08/M-11 | hourly | ACL drift heal (≤1h) |
| `screening-worker` (×lane) | M-09 | live/standard/bulk | PII + injection screening per lane |
| `extraction-worker` / `cascade-tier-worker` | M-10 | per-lane + batch | typed-atom extraction (cheap-first cascade) |
| `entity-resolution-worker` / `graph-upsert-worker` | M-11 | standard/batch | record linkage + provenance upsert |
| `embedding-worker` | M-12 | batch lane | embed chunks (`content_hash` dedupe) |
| `index-reconciler-worker` | M-12/M-03 | nightly | Postgres↔Qdrant drift heal |
| `run-executor-worker` | M-17 | BullMQ (interactive + batch) | execute agent runs (the universal lifecycle) |
| `run-checkpoint-worker` / `run-reaper-worker` | M-17 | BullMQ | checkpoint for replay; timeout reaping |
| `token-expiry-worker` | M-18 | BullMQ | enforce 5-min capability-token TTL |
| `tool-execution-worker` | M-19 | BullMQ | async/external-write tool calls |
| `eval-run-worker` | M-20 | CI-triggered + scheduled | release-gating evaluations |
| `chain-anchor-worker` / `chain-verify-worker` | M-21 | hourly + on-demand | WORM anchor + integrity verify |
| `clustering-worker` / `quantification-worker` | M-22 | standard | incremental feedback re-clustering |
| `diagnostic-worker` / `diagnostic-purge-worker` | M-23 | bulk lane | GTM diagnostic (never blocks live tenants) |
| `sync-push-worker` / `sync-state-reconciler-worker` | M-31 | BullMQ (retry/backoff) | one-way external push + coherence |
| `brief-publish-worker` / `brief-projection-worker` | M-32 | cron (local 6am) + event | publish Brief ≥99.5%; rebuild projections |
| `review-date-worker` | M-28 | daily | surface decisions due for review |
| `tide-ranking-worker` | M-37 | event-driven | calm re-ranking of notifications |
| `usage-rollup-worker` | M-34 | period | idempotent billing aggregation |
| `dsar-erasure-worker` | M-41 | BullMQ | GDPR cascade (≤24h, preserves chain) |
| `canvas-projection-worker` | M-35 | event-driven | rebuild canvas lens projections |
| `job-reaper-worker` | M-05 | cron | expire stale jobs, GC idempotency cache |
| `batch-lane-worker` / `provider-health-probe-worker` | M-07 | batch | autonomous model calls; provider health |
| `health-probe-worker` | M-42 | cron | subsystem health for `GET /ops/status` |

**Backpressure & honest degradation:** relay lag → freshness badge (never silent staleness); model provider degraded → lower tier + visible quality badge; bulk-lane backpressure → defer bulk, protect live; run dispatch is idempotent so token cost is never double-counted.

---

## 5. Agent Orchestration Architecture

**The logical run contract (the invariant):** every agent is a **versioned, stateless software unit** (role charter, tool manifest, memory lens, autonomy matrix, model binding, eval suite, KPIs). All state lives in the Task Workspace (working memory) + the memory plane, so **every run is replayable and auditable**. Year-1 implementation = NestJS orchestrator + BullMQ (M-17); Temporal is the named durable target at scale (TD-4).

**One lifecycle for all eleven agents (`POST /runs` → M-17):**
```
intake → plan → [for each step: acquire memory lens (M-16) → call Model Gateway (M-07)
       → (if tool needed) request capability token (M-18) → execute via Governed Tool Service (M-19)
       → record step + token cost (M-06) → checkpoint (M-17)]
       → assemble → eval gate (M-20) → submit draft to human (L1/L2) → (on approval) commit
```

**The eleven agents and their Year-1 status:**

| | Agent | Module | Year-1 status | Dispatched `task_class` | Writes / gate |
|---|---|---|---|---|---|
| S1 | Conductor | M-26 | **Active** | `conductor` (parent + child runs) | submits review package; child writes gated by `/approvals` |
| S2 | Sentinel | (M-37 seam → V2 M-43) | Tide ranking only | — | emits `tide_item`/`interrupt` |
| S3 | Archivist | (M-16/M-28 seam → V2 M-46) | hooks only | — | proposes ledger entries |
| A1 | Strategist | (M-30 seam → V2 M-51) | premortem scaffold | — | `/decisions/{id}/premortem` scaffold |
| A2 | Research | M-27 | **Active** | `research, synthesize_feedback` | L1 read-only — no token |
| A3 | Roadmap | M-39 | **Active (V1)** | `roadmap, sequence_roadmap` | re-sequence → `/decisions/commit` |
| A4 | Prioritization | M-40 | **Active (V1)** | `prioritization, rank_candidates` | ranking → `/decisions/commit` |
| A5 | PRD | M-24 | **Active** | `prd, draft_prd` | L2 — `/approvals` → `/prds` |
| A6 | Story Writing | M-25 | **Active** | `story, write_stories` | L2 — `/approvals` → commit hierarchy; external push via M-19/M-31 |
| A7 | Analytics | (M-15 seam → V2 M-48) | governed-number origin | — | arms/closes outcome windows (V2) |
| A8 | Release | (→ V2 M-49) | seam only | — | `/releases/{id}/readiness` (V2) |

**The five fully-active Year-1 agents:** Conductor, Research, PRD, Story, Roadmap + Prioritization (the living plan). The other six exist as seams.

**Orchestration controls:**
- **Parent/child token scoping (Conductor):** a child run receives only the capability tokens its task-class earned; the Conductor cannot escalate a child's authority.
- **Mandatory contrarian probe:** PRD (M-24), Research (M-27), Prioritization (M-40) all run an "evidence against" probe — structural confirmation-bias defense, not a prompt convention.
- **Numbers-as-tools:** any quantitative claim originates from `POST /metrics/query` (M-15); `metric_unavailable` → "unmeasurable," never invented.
- **Eval gate (M-20):** blocks sub-bar drafts before they reach a human; edit-distance feeds Trust-Ladder promotion.
- **Autonomy cap:** Year-1 issuance capped at L0–L2; L2 requires a human approval event; agent writes carry `ai_generated=true` + `source_run_id`. L3/L4 (TTL + two-person-rule + revert handles) land at Trust Ladder GA (V2) without changing the `/approvals` surface.

---

## 6. Multi-Tenant Boundaries

**One invariant: three independent enforcement layers must all fail for a leak (threat #1, existential).**

| Layer | Where it lives | Module | Mechanism |
|---|---|---|---|
| **1 — Postgres RLS** | system of record | M-01 | `organization_id` on every row; canonical RLS policy + `FORCE ROW LEVEL SECURITY`; per-request `SET LOCAL app.current_org_id` inside a tenant-scoped tx (PgBouncer transaction-pooling safe). The data layer **refuses any query lacking a TenantContext**. |
| **2 — Qdrant payload filter** | vector store | M-12 | payload-partitioned single-collection-per-granularity; `is_tenant`-indexed `organization_id` **force-injected from context, never request params**; Qdrant reachable only from the retrieval service (M-13). |
| **3 — Application ACL trim** | read path | M-02 + M-13 | source-ACL `read_principals` carried at upsert (M-11); pre-fusion trim on every read (RBAC × ABAC × source-ACL); honest abstention surfaces the *fact* of omission. |

**Boundary types:**
- **Organization = the hard boundary** (RLS-enforced). The unit of residency, billing, identity binding, and kill-switch scope. RLS guarantees no cross-org read.
- **Workspace = the soft boundary** (app-layer filter). Deliberate defense-in-depth split — join-based RLS would degrade query plans, so workspace is enforced in the application layer.
- **Stream = the ABAC narrowing unit** (membership-driven). Narrows what a member sees within a workspace (`stream_ids`, `max_sensitivity`).

**Tenant context flow:** verified Clerk JWT → BFF resolves `(organization_id, workspace_id, user_id, roles[], abac_attrs)` → opens tenant-scoped tx → every query + every Qdrant filter force-injects `organization_id`. **A request that cannot resolve a TenantContext is refused before any data is touched** (`403 tenant_context_unresolved`).

**Background workers** run `BYPASSRLS` but are **code-review-gated to filter `organization_id` explicitly**; the `cross-tenant-probe-worker` (M-01) runs nightly and any hit is a **sev-0** incident.

**Force-injection rule:** `organization_id`/`workspace_id` are **never** accepted from client params — passing them is ignored at best, `422` on conflict. This is mirrored in the Qdrant payload filter.

**Forward-compatibility (TD-1):** because `organization_id` is on every row and the cell stamp ships day one, the Year-2 **cell** migration (VPC + event bus + Postgres + Qdrant + Redis + KMS per cell; per-tenant Qdrant collections fail-closed; residency by region; dedicated cells for enterprise) is an **event-replay + router-flip, not a rewrite**. No client API changes.

---

## 7. Security Boundaries

**Threat model, ranked (from the spec):** (1) cross-tenant exposure — existential; (2) prompt injection driving unauthorized action — the AI-native attack; (3) source-credential theft; (4) insider/over-privileged access; (5) audit-record tampering; (6) supply chain.

| Threat | Defense (modules) | Mechanism |
|---|---|---|
| **#1 Cross-tenant** | M-01, M-12, M-13 | Three independent enforcement layers (RLS + Qdrant force-inject + ACL trim); nightly cross-tenant probe (sev-0 on hit). See Map 6. |
| **#2 Prompt injection → unauthorized action** | M-09, M-10, M-18, M-19 | **Defense-in-depth:** (a) ingestion screening → `quarantine:injection_suspect`, rendered inert (M-09); (b) structural prompt separation — source content only inside delimited typed evidence blocks (M-10); (c) tool schemas reject evidence-sourced args for sensitive parameters (M-19); (d) capability confinement — an agent's run holds a token only for the action class it earned (M-18); (e) continuous CI red-team, **tool-call attack-success-rate target 0**. |
| **#3 Credential theft** | M-08, M-02 | Source credentials live only in the secret store, never in PMOS tables, never returned by any API; webhooks HMAC-verified + deduped; passwords never touch PMOS (Clerk-only). |
| **#4 Insider / over-privileged** | M-02, M-13, M-41 | Pre-fusion ACL trim everywhere; RBAC (`viewer/editor/admin/owner`) × ABAC (Stream, residency, sensitivity); step-up MFA for sensitive ops; SOC 2 posture. |
| **#5 Audit tampering** | M-21, M-28, M-18, M-34 | Append-only hash-chained rows (`row_hash = H(prev_hash ‖ payload)`); `UPDATE`/`DELETE` revoked at role level; hourly chain-head anchored to WORM S3; chain-bound idempotency keys; `GET /audit/verify` (mismatch = sev-1). |
| **#6 Supply chain** | M-07, all | ZDR contracts at the Model Gateway; no cross-tenant training by default; dependency/secret hygiene; provider failover with honest quality badge. |

**The two-principal model (the structural core):**
- **Human authority = a per-request session claim** (RBAC × ABAC × source-ACL trim), resolved by M-02, enforced at every read/write. Computed, never stored long-lived.
- **Agent authority = a consumable capability token** bound to `(run_id, task_class, approval_event, ttl=5min)`, issued by the policy engine (M-18), verified cryptographically at the governed tool service (M-19). **An agent has no ambient authority** — an L1 agent cannot call a write tool because no token exists in its run.

**Security checkpoints by flow position:**
- **Entry:** JWKS verify + TenantContext resolution at the BFF (M-05) — checkpoint #1 in every flow.
- **Ingestion:** screening + quarantine (M-09) before any content is trusted.
- **Retrieval:** pre-fusion ACL trim + honest abstention (M-13).
- **Generation:** every sentence a `Claim` (M-14); numbers from the metric store (M-15); contrarian probe.
- **Action:** L2 approval issues a token (M-18) → verified at the tool service with sensitive-arg rejection (M-19).
- **Audit:** hash-chained append on every ceremonial/financial write (M-21).
- **Emergency:** per-tenant/agent/tool/level kill switch (M-42).

**Honest-degradation checkpoints (principle 8):** stale projection → freshness badge; model failover → lower tier + visible quality badge (`503 provider_degraded`, stream continues); ungroundable answer → abstention (counts to ≥95% honesty metric); unmeasurable number → "unmeasurable as predicted," never fabricated.


---

# Recommended Backend Build Sequence

This is the single ordered path from the first module to the last. It operationalizes the critical path in the Master Spec (§20) and the dependency waves in Map 1. Each step lists what unblocks it and the **exit gate** that must be green before the next step starts. Steps within the same wave that carry the same build-order number may proceed in parallel.

**Read this sequence as the engineering contract:** nothing below a line may begin until everything it depends on above the line has passed its exit gate. The longest pole — the ingestion→retrieval chain — is strictly sequential and is deliberately started in parallel with the Wave-0 platform work so it is not on the critical path alone.

## Wave 0 — Bedrock (no AI, no ingestion yet)

1. **M-01 Tenancy & RLS Kernel** + **M-03 Core Persistence** — built together as build-order 1. Nothing else may touch the database until `organization_id` force-injection, RLS policies, the `BYPASSRLS` worker convention, the cell stamp, and the soft-delete/UUIDv7/`timestamptz` conventions exist. *Exit gate:* nightly `cross-tenant-probe-worker` green; a query without a resolved `TenantContext` is refused `403`.
2. **M-02 Identity & Access** — Clerk JWKS verification, `TenantContext` resolution `(organization_id, workspace_id, user_id, roles[], abac_attrs)`, RBAC×ABAC. *Exit gate:* every downstream service can resolve a principal; no password material anywhere in PMOS.
3. **M-04 Event Backbone** — transactional outbox, the canonical envelope, the relay to Redis Streams, the Kafka seam. *Exit gate:* an event written in the same tx as its state change is delivered at-least-once and is consumer-idempotent.
4. **M-06 AI Schema Spine** + **M-07 Model Gateway** — build-order 4, in parallel. Schema spine (`agent_runs`, `agent_steps`, `claims`, token tables) and the provider-abstraction gateway with ZDR contracts, failover, and the honest quality badge. *Exit gate:* a stubbed run can be recorded end-to-end; provider failover emits `503 provider_degraded` without dropping the stream.
5. **M-05 BFF / API Gateway** — the single front door: JWKS verify, `TenantContext` injection, SSE fan-out, idempotency keys, rate limits. *Exit gate:* checkpoint #1 (entry auth + tenant resolution) enforced for every route.
6. **M-21 Audit Fabric** — append-only hash-chained ledger, WORM anchoring, `GET /audit/verify`. *Exit gate:* `UPDATE`/`DELETE` revoked at role level; chain-head anchored hourly; verify endpoint returns intact.

> **In parallel with all of Wave 0**, start the ingestion chain at **M-08 Connectors** (build-order 1 for the Knowledge group). It depends only on M-01/M-03/M-02 and is the longest pole, so it must not wait for the AI platform.

## Wave 1 — Knowledge Plane (the longest pole; strictly sequential)

7. **M-08 Connectors** — source registration, OAuth/secret-store handling, HMAC-verified deduped webhooks, sync scheduling. *Exit gate:* credentials live only in the secret store and are never returned by any API.
8. **M-09 Screening** — trust scoring, injection detection, `quarantine:injection_suspect` rendered inert. *Exit gate:* a known-malicious fixture is quarantined and never reaches extraction.
9. **M-10 Extraction** — normalization, chunking, structural prompt separation (source content only inside delimited typed evidence blocks). *Exit gate:* extracted content carries provenance and is structurally isolated from instructions.
10. **M-11 Entity Resolution** — dedup, canonical entities, the relational hierarchy as system of record. *Exit gate:* deterministic re-resolution; no cross-tenant entity bleed.
11. **M-12 Index Fan-Out** — embeddings via the gateway (`text-embedding-3-large`@3072d), Qdrant upsert with force-injected `organization_id` payload filter, lexical index. *Exit gate:* every vector carries its tenant filter; re-index is idempotent.
12. **M-13 Hybrid GraphRAG Retrieval** + **M-14 Claim[] / Provenance** — build-order together: native hybrid retrieval with **pre-fusion ACL trim** and honest abstention, plus the `Claim[]` wire protocol every AI sentence must carry. *Exit gate:* ungroundable query abstains; no result survives that the principal's ACL would deny.
13. **M-15 Metric Store** — the governed numeric source; every number in AI prose resolves here, never from the model. *Exit gate:* an unmeasurable metric returns "unmeasurable as predicted," never a fabricated value.
14. **M-16 Memory Plane** — durable run/agent memory with tenant scoping. *Exit gate:* memory reads are ACL-trimmed and tenant-isolated.

## Wave 2 — AI Platform (governed autonomy)

15. **M-17 Agent Runtime** — the NestJS + BullMQ orchestrator, run/step lifecycle, SSE streaming, the Temporal seam. *Exit gate:* a run progresses through steps with full `agent_runs`/`agent_steps` records.
16. **M-18 Policy Engine & Capability Tokens** — issues consumable tokens bound to `(run_id, task_class, approval_event, ttl=5min)`; L0–L2 via approval endpoints. *Exit gate:* an agent with no token cannot obtain write authority.
17. **M-19 Governed Tool Service** — token-verified tool execution with evidence-sourced-argument rejection on sensitive parameters. *Exit gate:* CI red-team tool-call attack-success-rate = 0.
18. **M-20 Eval Harness** — built **second, not last**; the regression/honesty/red-team gate that every subsequent agent ships behind. *Exit gate:* honesty ≥95% and abstention behavior measured in CI before any agent goes live.

> M-16 may land anywhere in Wave 2 once M-17 exists.

## Wave 3 — Product Intelligence & the MVP Loop

19. **M-26 Conductor** — the S1 orchestration agent that routes and sequences the worker agents. *Exit gate:* a multi-agent run is planned, dispatched, and reconciled.
20. **M-27 Research Agent** — first worker agent, validates the whole AI platform end-to-end on real retrieval. *Exit gate:* a grounded research answer ships with `Claim[]` and abstains when ungroundable.
21. **M-22 Feedback Intelligence** → **M-23 Diagnostic** — feedback ingestion/clustering, then diagnosis. *Exit gate:* feedback resolves to governed metrics and grounded claims.
22. **M-24 PRD Agent** → **M-25 Story Agent** — generation behind the eval gate; Story consumes PRD output. *Exit gate:* generated artifacts are fully claim-backed and traceable to sources.
23. **M-28 Decision Ledger ⇄ M-30 Commit Ceremony** — the fused decision-record + ceremony module; hash-chained, audited writes. *Exit gate:* a commit is irreversible-by-audit and replayable from the ledger.
24. **M-29 Ask-the-Brain** — the cross-corpus grounded Q&A surface. *Exit gate:* answers are ACL-trimmed, claim-backed, and abstain honestly.
25. **M-31 Living Sync v1** — keeps derived artifacts current as sources change. *Exit gate:* a source change propagates with a freshness badge, never a silent stale read.
26. **M-32 Standing Brief & Notify** — scheduled briefs and the notification fabric. *Exit gate:* briefs are tenant-scoped and ACL-trimmed at send time.
27. **M-33 The Line** — the prioritization/sequencing surface for product work. *Exit gate:* ordering is grounded and auditable.
28. **M-34 Metering** — usage capture for billing/limits across the platform. *Exit gate:* metering is chain-bound and tamper-evident.

## Wave 4 — Planning, Release & Administration

29. **M-35 Canvas Lens** → **M-36 Streams / Lenses / Membership** — the spatial surface, then the membership/Stream administration that drives ABAC narrowing. *Exit gate:* Stream membership changes immediately re-trim retrieval ACLs.
30. **M-37 Tide / Meridian Bar** — the fused temporal + command-bar administration surface. *Exit gate:* tenant-scoped, audited.
31. **M-38 Design System Support** — backend support for the design-system surface. *Exit gate:* no cross-tenant asset bleed.
32. **M-39 Roadmap Agent** → **M-40 Prioritization Agent** — the remaining Year-1 planning agents, behind the eval gate. *Exit gate:* outputs are claim-backed and abstain honestly.
33. **M-41 Compliance** — SOC 2 posture, residency, retention/DSAR machinery. *Exit gate:* residency and retention provably enforced per tenant.
34. **M-42 Resilience / Kill Switches** — per-tenant/agent/tool/level kill switches and degradation controls; built last so it can govern everything that exists. *Exit gate:* a single switch can halt any tenant, agent, tool, or autonomy level without a deploy.

---

## Critical-path summary

```
M-01+M-03 ─┬─► M-02 ─► M-04 ─► M-06+M-07 ─► M-05 ─► M-21         (Wave 0 platform)
           │
           └─► M-08 ─► M-09 ─► M-10 ─► M-11 ─► M-12 ─► M-13+M-14 ─► M-15 ─► M-16
                                                          (Wave 1 — longest pole)
                                                                   │
                        M-17 ─► M-18 ─► M-19 ─► M-20  ◄────────────┘   (Wave 2 AI)
                                                  │
        M-26 ─► M-27 ─► M-22 ─► M-23 ─► M-24 ─► M-25 ─► M-28⇄M-30 ─► …  (Wave 3 MVP)
                                                  │
        M-35 ─► M-36 ─► M-37 ─► M-38 ─► M-39 ─► M-40 ─► M-41 ─► M-42   (Wave 4)
```

**The two non-negotiable ordering laws:**
1. **M-01 is first, M-42 is last.** Tenant isolation must exist before any data; the kill-switch fabric must come last so it can govern every module already in place.
2. **The ingestion→retrieval chain (M-08→M-15) is strictly sequential and starts in Wave 0**, in parallel with platform work, because it is the longest pole. Retrieval (M-13) cannot ship before screening (M-09) makes content trustworthy, and no agent (Wave 2+) can be grounded before retrieval and the metric store exist.

---

## Build posture note

This document is **Year-1-authoritative**: every module is specified to its Year-1 implementation (shared-schema RLS, transactional outbox over Redis Streams, capability tokens via approval endpoints, NestJS+BullMQ orchestration, Qdrant native hybrid, Sonnet default behind the gateway). The **named end-state seams are preserved at every boundary** — the cell migration (TD-1), the Kafka/MSK cutover (TD-2), the Temporal cutover (TD-4), the generic-graph evolution (TD-6), and the OpenSearch option (TD-8) are each reachable as an event-replay or router-flip, **not a rewrite**. Build to the Year-1 column; leave the seams where this spec marks them.

*End of Backend_Modules.md*
