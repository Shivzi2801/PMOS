# PMOS — User Flows

### How Users Interact with PMOS, End to End

**Source documents:** `PMOS_MASTER_SPEC_Final.md` (Constitution v1.0) · `Feature_Inventory.md` (61 features, F-01…F-61)
**Scope of this document:** Per-feature interaction specs for **every Foundation, MVP, and V1 feature** (F-01 through F-42), followed by **ten end-to-end sequence flows** with bottlenecks, failure points, human-approval checkpoints, and security checkpoints called out.
**Intended use:** Detailed enough that engineering teams can design APIs and modules directly from the flows.

---

## Conventions used throughout

- **Actor shorthand** follows the spec personas: Alex (CPO), Sam (Senior PM), Olivia (Product Ops), Priya (Eng Lead), Eddie (CEO/CFO/Board), Fatima (APM), Carlos (Sales/CS). "Viewer" = any of the unlimited free read-only users.
- **APIs** follow the §15 three-protocol contract: **REST** `/api/v1/...` (commands + simple reads, `Idempotency-Key` mandatory on POST), **GraphQL** `/api/graphql` (lenses), **SSE** (streaming). The **BFF is the only client-facing surface** — no client ever calls a domain service, the Model Gateway, or a datastore directly.
- **`Claim[]`** is the wire type for every AI-generated prose field (`{text, citations[], kind, confidence}`).
- **Autonomy levels:** L0 observe · L1 draft · L2 act-with-approval · L3 act-and-notify · L4 autonomous. Year-1 caps at L1–L2.
- **Audit:** every auditable write appends a hash-chained row (`row_hash = H(prev_hash ‖ canonical_payload)`), hourly-anchored to WORM S3, OTel-traced.
- **Tenant context:** every request resolves a `TenantContext`; the data layer refuses any query lacking one. `SET LOCAL app.current_org_id` is set per request inside a tenant-scoped transaction.

A note on Foundation features: F-01…F-21 are mostly **platform substrate** with no direct end-user UI. For those, "User Goal / Journey" is written from the perspective of the *operator or developer persona who actually interacts with them* (admin, platform engineer, or — for the AI substrate — the agent invoking them on a user's behalf). Where a Foundation feature is exercised only transitively, that is stated, and the reader is pointed to the sequence flows in Part 2 where it appears in context.

---

# Part 1 — Per-Feature Interaction Specs

# 1. Foundation Features (F-01 – F-21)

---

## F-01 · Multi-Tenancy & Row-Level Security (RLS) Core

- **User Goal:** (Operator: Olivia/Platform Eng) Guarantee that one tenant can never read or write another tenant's data, on every query, automatically.
- **Entry Point:** Not user-facing. Exercised on *every* authenticated request as middleware; configured at workspace provisioning.
- **Trigger:** Any inbound request carrying a verified JWT → middleware resolves `(organization_id, workspace_id)` and opens a tenant-scoped transaction.
- **User Journey:** Transparent. The only visible artifact is failure: a request without resolvable tenant context is rejected before any data is touched.
- **Screens Involved:** None directly. (Admin sees the outcome in the Workspace settings / org boundary view.)
- **System Actions:** Resolve tenant context → `SET LOCAL app.current_org_id` → execute under `FORCE ROW LEVEL SECURITY` → org boundary enforced by RLS policy (hard), workspace boundary by app-layer filter (soft). Background workers run `BYPASSRLS` but must filter `organization_id` explicitly (code-review-gated).
- **AI Agents Involved:** None.
- **APIs Invoked:** None standalone; wraps all of them.
- **Database Entities Touched:** Every content row (all carry `organization_id` + `workspace_id`).
- **Success State:** Query executes scoped to exactly one org; cross-tenant rows are invisible at the database layer.
- **Failure States:** (1) Missing/unresolvable `TenantContext` → query refused (`403`/`422`). (2) RLS policy gap detected by nightly cross-tenant probe → **sev-0** incident. (3) Worker omits explicit filter → caught in code review / probe.
- **Notifications Generated:** Sev-0 page to on-call on any cross-tenant probe hit.
- **Audit Events Recorded:** Tenant-context resolution is part of the OTel trace; RLS denials logged. (No business-ledger entry.)

---

## F-02 · Identity & Access (Clerk Integration)

- **User Goal:** Sign in via the company's existing SSO and receive exactly the access their role allows — without PMOS ever holding a password.
- **Entry Point:** Login screen → Clerk-hosted SSO (SAML/OIDC); MFA challenge for editor+ roles.
- **Trigger:** User initiates sign-in, or SCIM provisions/deprovisions a user from the enterprise IdP.
- **User Journey:** User clicks "Sign in" → redirected to IdP → (MFA if editor+) → returns with a JWKS-verified JWT → lands in the Brief at their default altitude.
- **Screens Involved:** Login, SSO redirect, MFA challenge, first authenticated surface (the Brief).
- **System Actions:** Clerk issues JWT → BFF verifies via JWKS → resolves human authority = session claim (RBAC × ABAC × source-ACL trim) → binds `TenantContext`.
- **AI Agents Involved:** None.
- **APIs Invoked:** Clerk SSO/OIDC flow; `GET /api/v1/session` (resolve claims). Passwords never touch PMOS endpoints.
- **Database Entities Touched:** `users` (identity-source label via `auth_provider`, never a credential store), role/ABAC tables, SCIM-synced membership.
- **Success State:** Authenticated session with correct role; `TenantContext` bound.
- **Failure States:** SSO failure / expired assertion; MFA failure; SCIM deprovisioned user blocked; JWKS verification failure → `401`.
- **Notifications Generated:** Optional security alert on repeated auth failure; SCIM deprovision confirmation to admin.
- **Audit Events Recorded:** Sign-in, role grant/change, SCIM provision/deprovision (security audit log).
- **Security checkpoint:** This is checkpoint #1 in every flow — JWKS-verified JWT + role resolution.

---

## F-03 · Core Persistence Schema & Product Hierarchy

- **User Goal:** (Transitive) Have everything PMs and agents reason about — products, features, epics, stories, requirements, feedback, decisions, documents — stored as first-class, queryable objects.
- **Entry Point:** Not user-facing; the substrate every write lands in.
- **Trigger:** Any domain write (commit, ingest, generate, sync).
- **User Journey:** Invisible; surfaced only through the objects users manipulate elsewhere.
- **Screens Involved:** None directly.
- **System Actions:** UUIDv7 keys; `timestamptz` + `updated_at` trigger; soft delete (`deleted_at`, 30-day trash → purge). Polymorphism `(context_type, context_id)` only for "to-anything" edges.
- **AI Agents Involved:** None (but schema is AI-aware — see F-06).
- **APIs Invoked:** None standalone.
- **Database Entities Touched:** `organizations`, `workspaces`, `products`, `features`, `epics`, `user_stories`, `requirements`, `roadmaps`, `releases`, `feedback`, `interviews`, `insights`, `documents` (+ immutable versions + chunks).
- **Success State:** Writes land in typed first-class tables; reads are direct, not polymorphic-routed.
- **Failure States:** Constraint/FK violation; soft-deleted row referenced; UUID collision (practically nil with v7).
- **Notifications Generated:** None.
- **Audit Events Recorded:** `updated_at` change history; soft-delete events.

---

## F-04 · Transactional Outbox & Event Backbone

- **User Goal:** (Transitive) Never see a stale read or lose work because an event was dropped.
- **Entry Point:** Not user-facing; every state-changing write.
- **Trigger:** A domain write commits → outbox row written **in the same transaction** → relay worker publishes to Redis Streams → fan-out to projections/async work.
- **User Journey:** Invisible; manifests as fresh projections (the Brief, canvas) and timely async results.
- **Screens Involved:** None directly.
- **System Actions:** Outbox invariant (no event without the state change); relay worker drains outbox → Redis Streams; consumers update projections / enqueue jobs.
- **AI Agents Involved:** None (carries agent-run events).
- **APIs Invoked:** Internal only.
- **Database Entities Touched:** `outbox`, projection tables, event archive.
- **Success State:** Exactly-once-effective fan-out; projections rebuildable from the archive in minutes.
- **Failure States:** Relay lag (backpressure) → projection staleness → **honest degradation: UI renders freshness badge**; stream consumer crash → replay from archive.
- **Notifications Generated:** Ops alert on relay lag past SLO.
- **Audit Events Recorded:** Event archive is itself the durable record; not hash-chained unless the payload is auditable.
- **End-state seam:** Kafka/MSK swap is non-breaking (TD-2).

---

## F-05 · API Gateway / BFF & Three-Protocol Contract

- **User Goal:** (Developer/client) Talk to one stable, versioned surface for commands, lenses, and streams.
- **Entry Point:** Every client call goes through the BFF.
- **Trigger:** Any frontend interaction.
- **User Journey:** Invisible to end users; for developers, the dev fixture implements the exact `/api/v1` contract so frontend builds before the real BFF exists.
- **Screens Involved:** All (indirectly).
- **System Actions:** REST commands (`Idempotency-Key` mandatory on POST, 24h cache, body-hash mismatch → `409 idempotency_conflict`); GraphQL lenses (persisted queries only in prod, cost ≤ 1,000, depth ≤ 8, DataLoader batching); SSE streams (resumable via `Last-Event-ID`, 15s heartbeats, stream-ticket auth, 15-min server termination). One async-job grammar (`202 Accepted` + job resource + SSE progress + cancel).
- **AI Agents Involved:** None (carries `Claim[]` payloads).
- **APIs Invoked:** It *is* the API surface.
- **Database Entities Touched:** None directly (routes to domain services).
- **Success State:** Every client request served by the correct protocol; no client ever reaches a datastore or the Model Gateway directly.
- **Failure States:** Idempotency conflict (`409`); GraphQL complexity-budget breach (rejected pre-execution); SSE disconnect → client resumes via `Last-Event-ID`; rate-limit exhaustion.
- **Notifications Generated:** None to end users; rate-limit headers returned.
- **Audit Events Recorded:** Request tracing (OTel); ceremonial writes add hash-chain (see F-21).
- **Security checkpoint:** Single choke point for auth, ACL, and rate limiting.

---

## F-06 · AI Schema Spine (Agents, Runs, Conversations, Metering)

- **User Goal:** (Transitive / Olivia & finance) Have every agent action recorded, traceable, and billable to the token.
- **Entry Point:** Not user-facing; every agent run reads/writes here.
- **Trigger:** An agent run starts → `agent_runs` row created → steps logged to `agent_run_steps`.
- **User Journey:** Surfaced indirectly via run-progress UI and consumption metering.
- **Screens Involved:** Agent-run progress (in the Tide / object surfaces), metering dashboard (admin).
- **System Actions:** Record `ai_agents` (with `model` binding), `conversations`, `messages` (with `tool_calls`), token-metered `agent_runs`, step-traced `agent_run_steps`.
- **AI Agents Involved:** All eleven (this is their audit/cost spine).
- **APIs Invoked:** Internal writes during runs; `GET /api/v1/runs/{id}` for status; SSE run progress.
- **Database Entities Touched:** `ai_agents`, `conversations`, `messages`, `agent_runs`, `agent_run_steps`.
- **Success State:** Every run fully reconstructable from steps; token cost attributed per run.
- **Failure States:** Run crash mid-step → run marked failed, replayable from checkpoint (F-17); metering gap → reconciliation job.
- **Notifications Generated:** Run failure surfaced to the run's owner.
- **Audit Events Recorded:** The run/step trace is the audit; cost meters feed F-34.

---

## F-07 · Model Gateway

- **User Goal:** (Transitive) Get answers from the best-fit model under ZDR terms, with graceful degradation if a provider fails.
- **Entry Point:** Not user-facing; every LLM/embedding call routes through it.
- **Trigger:** An agent or retrieval step requests inference/embedding.
- **User Journey:** Invisible until degradation — then the user sees a **visible quality badge** on Ask answers.
- **Screens Involved:** None directly; the quality badge appears on Ask/Brief surfaces.
- **System Actions:** Per-task tiered routing (frontier/mid/small/embedding); default `claude-sonnet-4-6`; OpenAI `text-embedding-3-large` @ 3072 dims; ZDR contracts; no cross-tenant training; batch lanes for autonomous work; provider failover → lower tier + badge.
- **AI Agents Involved:** Serves all of them.
- **APIs Invoked:** Internal; reachable only from services, never clients.
- **Database Entities Touched:** Reads `ai_agents.model`; writes token usage to `agent_runs`.
- **Success State:** Inference returned within budget; correct tier selected.
- **Failure States:** Provider outage → failover + quality badge; rate-limit/quota → batch-lane queueing; embedding model change → blue/green collection swap.
- **Notifications Generated:** Quality-badge state on degraded answers.
- **Audit Events Recorded:** Token usage per call (cost spine).

---

## F-08 · Connector SDK & Source Connectors (≥6)

- **User Goal:** (Olivia) Connect PMOS to the org's real tools (Zendesk, Jira, Notion, Confluence, Slack, Linear; Gong/Salesforce as upgrades) so signal flows in automatically.
- **Entry Point:** Settings → Connectors → "Add a source."
- **Trigger:** Admin authorizes a connector via OAuth; thereafter webhooks/CDC push changes.
- **User Journey:** Olivia picks a source → completes the source's OAuth → grants read scope → sees a coverage estimate ("+ Gong would raise customer-voice coverage 41% → 78%") → connector begins backfill + live sync.
- **Screens Involved:** Connectors settings, OAuth consent (external), coverage/health view.
- **System Actions:** Store connector credential (in secret store, never in PMOS tables); register webhook/CDC; enqueue backfill (bulk lane); capture source ACL (`read_principals`).
- **AI Agents Involved:** None directly (feeds enrichment).
- **APIs Invoked:** `POST /api/v1/connectors` (Idempotency-Key); OAuth callback; `GET /api/v1/connectors/{id}/health`.
- **Database Entities Touched:** `connectors`, `connector_credentials` (ref to secret store), `ingestion_jobs`.
- **Success State:** Connector authorized, healthy, backfilling; live freshness ≤2 min on the live lane.
- **Failure States:** OAuth denied/scope insufficient; token expiry → re-auth prompt; webhook gap → CDC reconciliation; source rate-limit → backoff.
- **Notifications Generated:** Connector-health alerts (degraded/expired) to Olivia.
- **Audit Events Recorded:** Connector add/remove, scope grant, credential rotation (security audit).
- **Security checkpoint:** Source-credential custody (threat #3) — credentials in secret store; ACL data captured for downstream trim.

---

## F-09 · Ingestion Pipeline — Normalization, PII & Injection Screening

- **User Goal:** (Transitive / Olivia) Ingest external content safely — hostile content neutralized, PII handled — before it ever reaches an agent.
- **Entry Point:** Not user-facing; runs on every ingested item.
- **Trigger:** Connector delivers raw content (webhook/CDC/backfill) on one of three lanes (live ≤2 min / standard ≤15 min / bulk best-effort).
- **User Journey:** Invisible; manifests as trustworthy downstream signal. Quarantined items are flagged in connector health.
- **Screens Involved:** Connector health / quarantine review (Olivia).
- **System Actions:** Normalize → **PII screen** → **prompt-injection screen** (`quarantine:injection_suspect`, rendered inert) → pass to enrichment. "Ingested content is hostile until proven otherwise."
- **AI Agents Involved:** Screening classifiers (not the eleven agents).
- **APIs Invoked:** Internal pipeline; `GET /api/v1/ingestion/quarantine` for review.
- **Database Entities Touched:** `raw_items`, `ingestion_jobs`, `quarantine`.
- **Success State:** Clean, normalized content advances; suspect content quarantined inert.
- **Failure States:** Classifier false-negative (injection slips) → caught downstream by structural separation + CI red-team; false-positive → manual release from quarantine; lane backpressure → standard/bulk defer, live protected.
- **Notifications Generated:** Quarantine summary to Olivia.
- **Audit Events Recorded:** Quarantine decisions; PII-redaction events.
- **Security checkpoint:** Injection defense layer #1 (threat #2).

---

## F-10 · Signal Extraction & Enrichment (the "crown jewel")

- **User Goal:** (Transitive) Turn raw text into typed objects PMs can act on — `FeedbackAtom`s, `Commitment`s, `DecisionCandidate`s, `RiskSignal`s.
- **Entry Point:** Not user-facing; runs after screening.
- **Trigger:** Screened item enters enrichment.
- **User Journey:** Invisible; surfaces as clustered feedback, tracked promises, and ledger candidates.
- **Screens Involved:** None directly (outputs feed F-22, F-50, F-46).
- **System Actions:** Cheap-first cascade (~70% of records never touch an LLM) → typed extraction → confidence scoring.
- **AI Agents Involved:** Extraction models via Model Gateway; results later consumed by Research/Archivist agents.
- **APIs Invoked:** Internal; metered via `agent_runs`.
- **Database Entities Touched:** `feedback_atoms`, `commitments`, `decision_candidates`, `risk_signals`.
- **Success State:** Typed atoms produced with confidence + source pointer.
- **Failure States:** Low-confidence extraction → flagged `inference`, not asserted as fact; model error → retry/fallback tier.
- **Notifications Generated:** None (feeds downstream).
- **Audit Events Recorded:** Extraction run + token cost.

---

## F-11 · Entity Resolution & Graph Upsert

- **User Goal:** (Transitive) Have each signal attached to the right account/feature so it's findable and revenue-linked.
- **Entry Point:** Not user-facing; after extraction.
- **Trigger:** Typed atoms ready for resolution.
- **User Journey:** Invisible; surfaces as "this feedback is tied to Account X / Feature Y."
- **Screens Involved:** None directly.
- **System Actions:** Resolve atoms to canonical entities (e.g. `FeedbackAtom` → `Account`); upsert into hierarchy **with provenance**; attach source-ACL `read_principals` to every chunk/node at write time.
- **AI Agents Involved:** ER models via Gateway.
- **APIs Invoked:** Internal.
- **Database Entities Touched:** `accounts`, hierarchy tables, `provenance`, `links`, chunk `read_principals`.
- **Success State:** Atoms linked to canonical entities with resolvable provenance and ACLs.
- **Failure States:** Ambiguous match → low-confidence link flagged for review; ACL drift → reconciled ≤1h.
- **Notifications Generated:** None.
- **Audit Events Recorded:** Upsert + provenance write.
- **Security checkpoint:** ACL inheritance set here (load-bearing for all later retrieval trims).

---

## F-12 · Vector & Lexical Index Fan-Out (Qdrant)

- **User Goal:** (Transitive) Make everything retrievable fast, tenant-safely, and exactly (IDs, error strings).
- **Entry Point:** Not user-facing; after upsert.
- **Trigger:** New/changed `document_chunks` rows.
- **User Journey:** Invisible; manifests as fast, accurate search/Ask.
- **Screens Involved:** None directly.
- **System Actions:** Embed via Gateway → upsert to Qdrant where **chunk Postgres `id` = Qdrant point ID**; `content_hash` prevents redundant re-embedding; payload-partitioned single collection with `is_tenant`-indexed `organization_id` **force-injected from context**; int8 quantization; nightly reconciler heals drift.
- **AI Agents Involved:** Embedding model via Gateway.
- **APIs Invoked:** Internal; Qdrant reachable only from the retrieval service.
- **Database Entities Touched:** `document_chunks` (Postgres) ↔ Qdrant points.
- **Success State:** Chunks indexed; hybrid (dense+sparse) retrievable; vectors fully rebuildable from Postgres.
- **Failure States:** Embedding failure → retry; drift between Postgres/Qdrant → nightly reconciler; dimension change → blue/green collection.
- **Notifications Generated:** Ops alert on reconciler drift past threshold.
- **Audit Events Recorded:** Index fan-out events.
- **End-state seam:** OpenSearch added only if exact-match recall < ~0.95 on largest tenant (TD-8).

---

## F-13 · Hybrid GraphRAG Retrieval v1

- **User Goal:** (Transitive — powers Ask, artifacts, feedback) Get grounded, permission-safe, honest answers from the graph.
- **Entry Point:** Not user-facing standalone; invoked by Ask, artifact agents, feedback intelligence.
- **Trigger:** Any retrieval request from a feature/agent.
- **User Journey:** Invisible; its quality is the product's quality.
- **Screens Involved:** None standalone (outputs render in Ask/Brief/artifacts).
- **System Actions:** Parallel vector + lexical + typed-graph traversal + governed metric-store tool calls + ledger lookups → fusion → rerank (cross-encoder) → **ACL-trimmed select (pre-fusion)** → claim-grounded generation → groundedness verification. **Numbers are tools, not text.** Honest abstention: renders "n sources withheld by permissions."
- **AI Agents Involved:** Invoked by Research/PRD/Story/Conductor and Ask.
- **APIs Invoked:** Internal; surfaced via Ask SSE.
- **Database Entities Touched:** `document_chunks`, hierarchy, `provenance`, ledger, metric store.
- **Success State:** Claim-grounded answer with citations, ACL-correct, abstaining honestly when evidence is unseen/insufficient.
- **Failure States:** Insufficient grounding → abstain (counts toward ≥95% honesty metric); ACL-trim removes all evidence → "n withheld"; reranker miss → tiered fallback.
- **Notifications Generated:** None directly.
- **Audit Events Recorded:** Retrieval run + which sources were ACL-trimmed (without leaking content).
- **Security checkpoint:** Pre-fusion ACL trim — PMOS never widens access.

---

## F-14 · Claim[] Protocol & Provenance Substrate

- **User Goal:** Trust every generated sentence because it's traceable to a source in <400ms.
- **Entry Point:** Every AI prose surface (Brief, Ask, PRD, story, report).
- **Trigger:** Any generation produces prose → emitted as `Claim[]`.
- **User Journey:** User reads a generated sentence → sees the **Provenance Underline** (1px single / 2px corroborated / dotted inference / violet simulated) → clicks → Provenance Lens opens the source(s) in <400ms.
- **Screens Involved:** Provenance Lens; every artifact/evidence surface.
- **System Actions:** Bind citations to sentences; mark uncitable claims `inference`; render redundant non-color encoding (thickness + glyph) for accessibility.
- **AI Agents Involved:** All generative agents emit `Claim[]`.
- **APIs Invoked:** `GET /api/v1/provenance/{claimId}` (must resolve <400ms); `Claim[]` is a wire invariant on every prose payload.
- **Database Entities Touched:** `claims`, `citations`, `provenance`, source chunks.
- **Success State:** Every sentence resolvable to its evidence; inference visibly distinguished from fact.
- **Failure States:** Citation resolution >400ms → SLO breach surfaced (degraded badge); orphaned citation → claim downgraded to `inference`.
- **Notifications Generated:** None.
- **Audit Events Recorded:** Claim/citation creation linked to the generating run.

---

## F-15 · Governed Metric Store

- **User Goal:** (Transitive / Eddie) Trust every number because it came from a governed source, not generated text.
- **Entry Point:** Not user-facing; called as a tool during retrieval/generation.
- **Trigger:** Any quantitative claim required → governed metric-store tool call.
- **User Journey:** Invisible; manifests as inspectable numbers in reports/scorecards.
- **Screens Involved:** None standalone (numbers render with inspectable join logic in reports).
- **System Actions:** Serve metrics via governed tool calls with inspectable join logic; refuse to let prose invent numbers.
- **AI Agents Involved:** Analytics, Prioritization, PRD (any agent citing a number).
- **APIs Invoked:** Internal tool interface.
- **Database Entities Touched:** Metric store tables/views.
- **Success State:** Every number originates from a governed call and is inspectable.
- **Failure States:** Metric unavailable → claim marked "unmeasurable as predicted," never fabricated; stale metric → freshness surfaced.
- **Notifications Generated:** None.
- **Audit Events Recorded:** Metric tool calls (cost + provenance).

---

## F-16 · Memory Plane (Four Cognitive Types × Three Scopes)

- **User Goal:** (Transitive) Have PMOS remember context so agents don't start from zero — the compounding moat.
- **Entry Point:** Not user-facing; agents read/write memory each run.
- **Trigger:** Any agent run with a memory lens.
- **User Journey:** Invisible; manifests as agents "knowing" org/product/user context and anti-patterns.
- **Screens Involved:** Procedural-memory governance (Olivia edits templates/scoring models/anti-patterns).
- **System Actions:** Working (task workspace), episodic (ledger + run logs, permanent), semantic (graph/indexes), procedural (human-governed) memory, across org/product/user scopes.
- **AI Agents Involved:** All eleven, each via its memory lens.
- **APIs Invoked:** Internal; procedural edits via REST commands (Olivia).
- **Database Entities Touched:** `memory_*` stores, `templates`, `scoring_models`, `anti_patterns`, ledger, indexes.
- **Success State:** Agents operate with correct scoped context; procedural memory is human-governed.
- **Failure States:** Stale/conflicting memory → consolidation by Archivist (V2); bad procedural edit → versioned, revertible.
- **Notifications Generated:** None.
- **Audit Events Recorded:** Procedural-memory edits (who/what/when, versioned).

---

## F-17 · Agent Runtime (Stateless, Checkpointed, Replayable Runs)

- **User Goal:** (Transitive) Trust that every autonomous action can be audited and replayed exactly.
- **Entry Point:** Not user-facing; every agent task runs here.
- **Trigger:** Conductor (or a surface) dispatches a task → NestJS orchestrator + BullMQ schedules a run.
- **User Journey:** Visible as run progress in the Tide / object surfaces; "what is the AI doing, under what authority."
- **Screens Involved:** Run-progress views; agent activity in the Tide.
- **System Actions:** Stateless, checkpointed, capability-gated runs; all state in Task Workspace + memory; replay from checkpoint.
- **AI Agents Involved:** All eleven execute here.
- **APIs Invoked:** `POST /api/v1/runs` (`202` + job resource); SSE run progress; `POST /api/v1/runs/{id}/cancel`.
- **Database Entities Touched:** `agent_runs`, `agent_run_steps`, task workspace.
- **Success State:** Run completes, fully replayable; output queued for human review at L1/L2.
- **Failure States:** Step failure → checkpoint replay; tool denied (no token) → run halts at boundary; timeout → cancel + resumable.
- **Notifications Generated:** Run completion/failure to owner.
- **Audit Events Recorded:** Full run/step trace.
- **End-state seam:** Temporal swap is mechanical (TD-4).

---

## F-18 · Policy Engine v1 & Capability Tokens (L0–L2)

- **User Goal:** (Alex/Olivia) Be certain an agent can only do what it's been explicitly, currently authorized to do.
- **Entry Point:** Not user-facing as a screen; enforced at every agent tool call. Authority grants happen via the Trust Ladder UI (V2 for promotion) and per-action approval endpoints (Year-1).
- **Trigger:** An agent attempts a capability-bearing action → policy engine checks/issues a token bound to (run, task-class, approval event, 5-min TTL).
- **User Journey:** For L2 actions, the human sees an **approval prompt**; approving issues the token; the action then proceeds.
- **Screens Involved:** Approval prompts on artifact/sync surfaces.
- **System Actions:** Issue consumable capability token; cryptographic verification at the tool service; `ai_generated=true` + `source_run_id` on agent writes (Year-1).
- **AI Agents Involved:** All write-capable agents are gated here.
- **APIs Invoked:** `POST /api/v1/approvals/{runId}` (human approval → token issuance).
- **Database Entities Touched:** `capability_tokens`, `approvals`, autonomy log.
- **Success State:** Authorized action proceeds with a valid, time-boxed token; unauthorized action is impossible (no token exists).
- **Failure States:** No/expired token → action refused at tool service; approval declined → run ends without acting.
- **Notifications Generated:** Approval requests to the responsible human.
- **Audit Events Recorded:** Token issuance, approval event, autonomy log (hash-chained).
- **Human-approval checkpoint:** This is the Year-1 L2 gate.
- **Security checkpoint:** Capability confinement (threat #2 final layer).

---

## F-19 · Governed Tool Service

- **User Goal:** (Transitive) Have agent actions execute only through a service that verifies authority and rejects injected arguments.
- **Entry Point:** Not user-facing; the execution point for all agent tool calls.
- **Trigger:** An agent invokes a tool with a capability token.
- **User Journey:** Invisible; the enforcement point behind every L2/L3 action.
- **Screens Involved:** None.
- **System Actions:** Verify token cryptographically; **reject evidence-sourced arguments for sensitive parameters**; execute against external system (e.g. Jira) or internal mutation.
- **AI Agents Involved:** Story/Release/Sync agents (write tools); all tool-using agents.
- **APIs Invoked:** Internal tool interface; external connectors for writes.
- **Database Entities Touched:** Depends on tool (e.g. sync state, hierarchy).
- **Success State:** Tool executes with verified authority and clean arguments.
- **Failure States:** Token invalid → refused; sensitive param sourced from evidence → rejected; external system error → compensating action / surfaced.
- **Notifications Generated:** None directly.
- **Audit Events Recorded:** Every tool call (input/output, token, run).
- **Security checkpoint:** CI red-team target = 0 tool-call attack success.

---

## F-20 · Eval Harness (Release-Gating)

- **User Goal:** (Priya/Olivia) Be confident generated quality clears the engineer bar before anything ships.
- **Entry Point:** CI pipeline (developer-facing) + quality dashboards (Olivia/Priya).
- **Trigger:** Any release of an agent/prompt/model change → eval harness runs; also continuous measurement of accepted-artifact edit-distance.
- **User Journey:** Developer pushes change → harness runs gold-standard evals → release blocked if quality regresses; Priya watches edit-distance/approval-latency trend toward the advocacy band.
- **Screens Involved:** CI results; quality/edit-distance dashboards.
- **System Actions:** Measure normalized edit-distance on accepted stories, time-to-approval per (team, task-type), groundedness, honesty/abstention (≥95%); encode kill/pivot trigger (>30% edit-distance after two quarters).
- **AI Agents Involved:** Evaluates all generative agents.
- **APIs Invoked:** Internal CI; `GET /api/v1/quality/metrics`.
- **Database Entities Touched:** `eval_runs`, `edit_distance_records`, gold-standard sets.
- **Success State:** Release passes gates; quality trend visible and within band.
- **Failure States:** Regression → release blocked; edit-distance >30% after tuning window → kill/pivot trigger fires (scope freeze).
- **Notifications Generated:** CI failure to engineering; trend alerts to Olivia/Priya.
- **Audit Events Recorded:** Eval results per release.
- **Human-approval checkpoint:** Quality gate is a release blocker (Priya's veto encoded as an instrument).

---

## F-21 · Audit Fabric (Append-Only Hash-Chained Ledger Substrate)

- **User Goal:** (Eddie/security reviewer) Trust that auditable records can't be tampered with.
- **Entry Point:** Not user-facing; underpins ledger, autonomy log, billing meters.
- **Trigger:** Any auditable write.
- **User Journey:** Invisible until inspection — then a security review or M&A diligence (Year-3) verifies the chain.
- **Screens Involved:** Audit timeline / verification view (admin/reviewer).
- **System Actions:** Append-only Postgres rows, `row_hash = H(prev_hash ‖ canonical_payload)`; hourly chain-head anchor to WORM S3; OTel trace linkage; `UPDATE`/`DELETE` revoked at the role level.
- **AI Agents Involved:** None.
- **APIs Invoked:** Internal append; `GET /api/v1/audit/verify`.
- **Database Entities Touched:** `decision_ledger`, `autonomy_log`, `billing_meters` (all append-only).
- **Success State:** Tamper-evident chain; verifiable head anchored to WORM.
- **Failure States:** Chain-head mismatch → integrity alert (sev-1); attempted UPDATE/DELETE → blocked at role level.
- **Notifications Generated:** Integrity alerts to security on chain mismatch.
- **Audit Events Recorded:** It *is* the audit record.
- **Security checkpoint:** Audit-tampering defense (threat #5).

---

# 2. MVP Features (F-22 – F-34)

---

## F-22 · Feedback Intelligence (the wedge)

- **User Goal:** (Sam) Read what customers are saying at scale — clustered, quantified, tied to accounts/revenue, threaded to the decisions it should inform.
- **Entry Point:** The Brief (a finding leads with it) or the Line ("show feedback on billing").
- **Trigger:** New feedback ingested + clustered, or PM opens the feedback lens.
- **User Journey:** Sam opens the Brief → sees "Billing friction up 3× on enterprise accounts ($1.2M ARR)" → opens the cluster lens → drills into atoms, each with provenance → threads the cluster to a decision or PRD.
- **Screens Involved:** Brief, feedback cluster lens, Provenance Lens, Stream canvas.
- **System Actions:** Cluster `FeedbackAtom`s → quantify (account/revenue join via metric store) → thread to decisions/artifacts; render `Claim[]` with provenance.
- **AI Agents Involved:** Research (A2); Sentinel (V2) for risk threading.
- **APIs Invoked:** GraphQL feedback lens; `GET /api/v1/provenance/{id}`; metric-store tool calls.
- **Database Entities Touched:** `feedback_atoms`, `accounts`, `insights`, `claims`, metric store.
- **Success State:** Sam sees quantified, cited clusters and can act in one move; ≥5–6 hrs/week recovered.
- **Failure States:** Sparse signal → low-confidence clusters flagged; ACL-trimmed atoms → "n withheld"; metric join unavailable → unquantified but still cited.
- **Notifications Generated:** Tide item when a cluster crosses a risk/volume threshold.
- **Audit Events Recorded:** Cluster/insight creation; any decision threading.

---

## F-23 · The Free Diagnostic (GTM)

- **User Goal:** (Prospect Alex) See the synthesis gap in my own data before buying.
- **Entry Point:** Marketing site / sales-led trial → "Run the Diagnostic."
- **Trigger:** Prospect authorizes read-only connectors → diagnostic job launched.
- **User Journey:** Alex connects Zendesk + Notion (read-only) → diagnostic ingests on the bulk lane → async job runs → Alex receives a findings report ("here's what you're not seeing," with coverage estimate and honest gaps).
- **Screens Involved:** Connector consent, diagnostic progress (async job), findings report.
- **System Actions:** Scoped ingestion → enrichment → synthesis → findings report (all `Claim[]`); coverage estimate surfaced.
- **AI Agents Involved:** Research (A2).
- **APIs Invoked:** `POST /api/v1/diagnostic` (`202` + job + SSE progress + cancel); report via GraphQL.
- **Database Entities Touched:** Scoped `feedback_atoms`, `insights`, `diagnostic_runs`.
- **Success State:** Prospect sees a credible, cited findings report proving 30-day ROI on tickets+docs alone.
- **Failure States:** Insufficient data → honest "low coverage" result; connector auth fails → guided retry; job timeout → resumable.
- **Notifications Generated:** "Your Diagnostic is ready" to the prospect.
- **Audit Events Recorded:** Diagnostic run (scoped, time-boxed); data purged per trial policy.
- **Security checkpoint:** Read-only scopes; trial data isolation + purge path.

---

## F-24 · Artifact Engine — Evidence-Native PRD (PRD Agent, L1/L2)

- **User Goal:** (Sam) Turn a committed decision into a build-ready PRD where every sentence traces to evidence.
- **Entry Point:** Decision Sheet → "Draft PRD," or Conductor delegation, or the Line ("draft the PRD for this decision").
- **Trigger:** A committed decision (or explicit request) dispatches an L2 PRD run.
- **User Journey:** Sam commits a decision → PRD agent drafts evidence-native spec (with mandatory contrarian probe) → queued for review → Sam edits non-goals (edit-distance logged) → **approves (L2 human checkpoint)** → PRD versioned.
- **Screens Involved:** Decision Sheet, PRD draft/review surface (serif artifact typography), Provenance Lens, approval prompt.
- **System Actions:** Retrieve (F-13) → claim-grounded generation → contrarian probe ("evidence against") → queue draft → on approval, version + flag `ai_generated`/`source_run_id`.
- **AI Agents Involved:** PRD (A5); Conductor (A1) for orchestration; Research (A2) for evidence.
- **APIs Invoked:** `POST /api/v1/runs` (PRD task, `202`); SSE progress; `POST /api/v1/approvals/{runId}`; `POST /api/v1/prds` on commit.
- **Database Entities Touched:** `documents` (+immutable version + chunks), `claims`, `citations`, `agent_runs`, `edit_distance_records`.
- **Success State:** Approved, versioned, fully-cited PRD; edit-distance within band.
- **Failure States:** Insufficient evidence → sections marked `inference`; eval gate fail → not surfaced; approval declined → draft discarded/iterated.
- **Notifications Generated:** "PRD draft ready for review" to Sam.
- **Audit Events Recorded:** Run, draft, edits (edit-distance), approval (autonomy log).
- **Human-approval checkpoint:** L2 approval before the PRD is committed.

---

## F-25 · Artifact Engine — Story Writing (Story Agent, L1/L2)

- **User Goal:** (Sam → Priya) Turn an approved PRD into engineer-grade epics/stories/ACs.
- **Entry Point:** Approved PRD → "Generate stories," or Conductor delegation.
- **Trigger:** Approved PRD dispatches an L2 story run.
- **User Journey:** From the approved PRD, Story agent generates epic tree + stories + ACs (evidence-native) → queued → Sam/Priya review → **approve (L2)**. (The L3 push to Jira is V2 / F-47; Year-1 stays at draft+approve.)
- **Screens Involved:** Story review surface, AC detail, Provenance Lens, approval prompt.
- **System Actions:** Retrieve PRD + evidence → generate epics/stories/ACs as `Claim[]` → measure edit-distance + approval latency per (team, task-type).
- **AI Agents Involved:** Story Writing (A6); PRD (A5) upstream.
- **APIs Invoked:** `POST /api/v1/runs` (story task); SSE; `POST /api/v1/approvals/{runId}`.
- **Database Entities Touched:** `epics`, `user_stories`, `requirements`, `claims`, `edit_distance_records`.
- **Success State:** Engineer-approved stories within edit-distance band (the P4 advocacy threshold).
- **Failure States:** Edit-distance >30% sustained → kill/pivot trigger (F-20); approval declined → iterate.
- **Notifications Generated:** "Stories ready for review."
- **Audit Events Recorded:** Run, edits, approval.
- **Human-approval checkpoint:** L2 approval (Priya's quality bar).

---

## F-26 · Conductor Agent (AI Chief of Staff)

- **User Goal:** (Sam) Hand off a goal and have it planned, delegated, assembled, and returned for decision.
- **Entry Point:** The Line ("Do" mode), the Brief recommendations, or scheduled work.
- **Trigger:** A user request or finding that requires multi-agent work.
- **User Journey:** Sam asks "prep the billing decision" → Conductor plans → delegates to Research/PRD/Prioritization → assembles results → submits a review package to Sam.
- **Screens Involved:** The Line, run-progress, assembled review package.
- **System Actions:** Intake → plan → delegate (capability-scoped) → assemble → submit to human.
- **AI Agents Involved:** Conductor (S1) orchestrating A1–A8.
- **APIs Invoked:** `POST /api/v1/runs` (orchestration); SSE; sub-run dispatch internal.
- **Database Entities Touched:** `agent_runs` (parent+children), task workspace.
- **Success State:** Coherent assembled package submitted to the human for judgment only.
- **Failure States:** Sub-run failure → partial assembly + honest gap; delegation denied (no capability) → halted at boundary.
- **Notifications Generated:** "Your package is ready" to the requester.
- **Audit Events Recorded:** Parent/child run trace.

---

## F-27 · Research Agent

- **User Goal:** (Sam/Fatima) Get decision-ready evidence synthesis from feedback, interviews, and market context.
- **Entry Point:** Conductor delegation, the Line ("synthesize feedback on onboarding"), or Feedback Intelligence.
- **Trigger:** Research task dispatched.
- **User Journey:** Request → Research agent retrieves + synthesizes → returns cited synthesis with a contrarian view.
- **Screens Involved:** Research output surface, Provenance Lens.
- **System Actions:** Retrieve (F-13) → synthesize as `Claim[]` → confidence + counter-evidence.
- **AI Agents Involved:** Research (A2).
- **APIs Invoked:** `POST /api/v1/runs`; SSE; provenance reads.
- **Database Entities Touched:** `feedback_atoms`, `interviews`, `insights`, `claims`.
- **Success State:** Cited, balanced synthesis ready to inform a decision.
- **Failure States:** Thin evidence → flagged; ACL trim → "n withheld."
- **Notifications Generated:** Completion to requester.
- **Audit Events Recorded:** Run + sources cited.

---

## F-28 · Decision Ledger v1

- **User Goal:** (Alex/Sam) Have every decision recorded as a versioned object — options, evidence, assumptions, predicted impact, owner, dissent, review date.
- **Entry Point:** Created as a **byproduct** of the commit ceremony (F-30); read via the Line, Brief, or Ask.
- **Trigger:** A commit ceremony fires (approving a PRD, committing an Arena ranking).
- **User Journey:** The decision object is assembled from what the human did — no standalone "log a decision" form. Anyone later reads it via Ask ("why don't we support SSO on Starter?").
- **Screens Involved:** Decision Sheet (read), Ask answers, Brief.
- **System Actions:** Assemble decision from the action → append hash-chained ledger entry (F-21) → index for retrieval.
- **AI Agents Involved:** Archivist (V2) mines `DecisionCandidate`s to fill it further.
- **APIs Invoked:** Written by the commit command; read via GraphQL/Ask.
- **Database Entities Touched:** `decision_ledger` (append-only), `decision_options`, `assumptions`, `dissent`, `claims`.
- **Success State:** >40% of committed decisions originate as a byproduct of an existing action.
- **Failure States:** <40% byproduct after a quarter → treat as product-design defect (redesign commit surfaces, not add a ritual).
- **Notifications Generated:** Review-date reminders.
- **Audit Events Recorded:** The ledger entry itself (hash-chained, append-only).

---

## F-29 · Ask-the-Brain v1 (Org-Wide Product Brain)

- **User Goal:** (Any viewer) Ask "why" about any product decision and get decision + evidence + owner + review date.
- **Entry Point:** The Line ("Ask" mode), `⌘K`.
- **Trigger:** User types a question.
- **User Journey:** User asks → first token <700ms → claims stream in over SSE, each with provenance → if evidence is unseen/insufficient, honest abstention ("n sources withheld" / "I can't ground this").
- **Screens Involved:** The Line, streaming answer surface, Provenance Lens.
- **System Actions:** Retrieve (F-13) → claim-grounded generation → groundedness verification → ACL-trimmed → stream `Claim[]`.
- **AI Agents Involved:** Retrieval-backed Ask (not a standalone specialist); may invoke Research.
- **APIs Invoked:** SSE Ask stream (stream-ticket auth); `GET /api/v1/provenance/{id}`.
- **Database Entities Touched:** Indexes, hierarchy, `decision_ledger`, `claims`.
- **Success State:** Grounded, cited, permission-correct answer; honest abstention where due (≥95% on unanswerable sets).
- **Failure States:** No groundable evidence → abstain; degraded model → quality badge; ACL trim → "n withheld."
- **Notifications Generated:** None.
- **Audit Events Recorded:** Ask run, sources cited, abstention events.
- **Security checkpoint:** Pre-fusion ACL trim; never leaks the existence-of-omission.

---

## F-30 · Commit Ceremony & Decision Sheet

- **User Goal:** (Sam) Make and record a decision with appropriate ceremony — and have it reversible and cited.
- **Entry Point:** Decision Sheet (from the Brief finding, a feedback cluster, or the Line).
- **Trigger:** Human chooses to commit (typed initial = the ceremony).
- **User Journey:** Sam opens the Decision Sheet → reads The Question + The Call → runs a **Pre-Mortem** (synthetic stakeholders, V2/F-51) → adds a guard ("gate at 5% until A3 verifies") → types initial → **commit** writes the hash-chained ledger entry.
- **Screens Involved:** Decision Sheet, Pre-Mortem panel, commit confirmation.
- **System Actions:** BFF → domain service → Postgres tx (state + outbox) → **hash-chain append + signature verification** (ceremonial write).
- **AI Agents Involved:** Strategist (Pre-Mortem, V2); Conductor for follow-on work.
- **APIs Invoked:** `POST /api/v1/decisions/commit` (`Idempotency-Key`, ceremonial).
- **Database Entities Touched:** `decision_ledger`, `assumptions`, `guards`, outbox.
- **Success State:** Decision committed, ledgered, reversible; downstream work (PRD) can be dispatched.
- **Failure States:** Signature/verification failure → commit rejected; idempotency conflict → `409`; concurrent edit → last-write-wins with version guard.
- **Notifications Generated:** Owner + dissenters notified; review-date scheduled.
- **Audit Events Recorded:** The commit (hash-chained, signed).
- **Human-approval checkpoint:** The commit *is* the human judgment ceremony.
- **Security checkpoint:** Signature verification on ceremonial write.

---

## F-31 · Living Sync v1 (One-Way Spec → Jira/Linear/ADO)

- **User Goal:** (Sam/Priya) Push the approved spec to the execution tool with diffs + rationale, keeping them coherent.
- **Entry Point:** Approved stories → "Sync to Jira," or post-approval automation.
- **Trigger:** Approved epics/stories dispatch a one-way sync.
- **User Journey:** After story approval, Sam triggers sync → diffs + rationale shown → **approve (L2)** → epic tree pushed to Jira → sync status tracked.
- **Screens Involved:** Sync preview (diffs + rationale), approval prompt, sync status.
- **System Actions:** Compute diff → generate rationale (`Claim[]`) → on approval, push via governed tool service (capability token) → record external IDs + sync state.
- **AI Agents Involved:** Story Writing (A6); Conductor.
- **APIs Invoked:** `POST /api/v1/sync` (`202`); approval endpoint; external connector write via F-19.
- **Database Entities Touched:** `sync_state`, `external_refs`, hierarchy.
- **Success State:** Spec reflected in Jira with traceable rationale; coherence baseline established.
- **Failure States:** External API error → partial push surfaced (full saga/rollback is V2 with Temporal); auth expiry → re-auth; conflict → flagged drift.
- **Notifications Generated:** Sync success/failure to Sam.
- **Audit Events Recorded:** Sync action (tool call, token, external IDs).
- **Human-approval checkpoint:** L2 approval before any external write.
- **Security checkpoint:** Capability token verified at the tool service.

---

## F-32 · The Brief / Standing Brief v1 + Notify

- **User Goal:** (Alex/Sam) Open PMOS and immediately see what changed, what it means, and what's recommended — never an empty screen.
- **Entry Point:** App open (default surface); published by local 6am ≥99.5% of days.
- **Trigger:** Scheduled 6am render + on-demand re-render from the ledger.
- **User Journey:** Sam opens PMOS → the Brief leads with the top finding (cited) → recommended actions inline → drills into any claim via the Provenance Lens.
- **Screens Involved:** The Brief, Provenance Lens, recommended-action surfaces.
- **System Actions:** Re-render narrative from the ledger (never stored stale) → rank findings → emit `Claim[]`; "the system speaks first."
- **AI Agents Involved:** Sentinel (V2) feeds findings; retrieval-backed narrative.
- **APIs Invoked:** GraphQL Brief lens; cold load to interactive Brief <1.5s.
- **Database Entities Touched:** `decision_ledger`, projections, `claims`.
- **Success State:** Current, cited Brief on open; saves status-report time.
- **Failure States:** Projection stale → freshness badge (honest degradation); render miss → fallback to last-good with staleness marker.
- **Notifications Generated:** The Brief itself + the Tide; digest notify.
- **Audit Events Recorded:** None for reads; findings reference their source runs.

---

## F-33 · The Line (Command Interface) & Search v1

- **User Goal:** (Anyone) Do everything from one command interface — Go (navigate), Ask (question), Do (act).
- **Entry Point:** `⌘K` from any surface.
- **Trigger:** User opens the Line and types.
- **User Journey:** User hits `⌘K` → types → intent routed: **Go** <50ms navigation, **Ask** streams claims (F-29), **Do** dispatches an agent task (F-26). 100% pointer-free.
- **Screens Involved:** The Line overlay; target surface.
- **System Actions:** Intent classification (Go/Ask/Do) → route to search / Ask / Conductor.
- **AI Agents Involved:** Conductor (Do); Ask retrieval (Ask).
- **APIs Invoked:** Search (Go) <50ms; Ask SSE; `POST /api/v1/runs` (Do).
- **Database Entities Touched:** Indexes (Go), everything downstream (Ask/Do).
- **Success State:** One keystroke surface reaches any capability; Go <50ms.
- **Failure States:** Ambiguous intent → disambiguation prompt; no result → honest empty state ("retrieval failed — try Ask").
- **Notifications Generated:** None (entry point).
- **Audit Events Recorded:** Do-mode dispatches are run-traced.

---

## F-34 · Consumption Metering & Billing Meters

- **User Goal:** (Alex/finance) See and pay for autonomy work transparently — platform fee + metered units side by side.
- **Entry Point:** Admin → Billing / Usage.
- **Trigger:** Every agent run accrues token cost → meter; billing cycle aggregates.
- **User Journey:** Alex opens Usage → sees autonomy units consumed per agent/task-type, mapped to cost → chooses platform-fee vs consumption mix.
- **Screens Involved:** Usage dashboard, billing settings.
- **System Actions:** Read `agent_runs` token meters → append billing meters (hash-chained, F-21) → aggregate.
- **AI Agents Involved:** None (reads their meters).
- **APIs Invoked:** GraphQL usage lens; `GET /api/v1/billing/meters`.
- **Database Entities Touched:** `agent_runs`, `billing_meters` (append-only).
- **Success State:** Accurate, inspectable usage + cost; pricing-mix choice supported.
- **Failure States:** Metering gap → reconciliation job; dispute → inspectable per-run trace.
- **Notifications Generated:** Usage-threshold alerts to Alex.
- **Audit Events Recorded:** Billing meters (append-only, hash-chained).

---

# 3. V1 Features (F-35 – F-42)

---

## F-35 · The Meridian Canvas & Altitudes

- **User Goal:** (Sam/Alex) See the org's state on one spatial canvas, zoomable from 30k ft to a single object.
- **Entry Point:** App shell (the canvas is the persistent surface); altitude control on the Meridian Bar.
- **Trigger:** User pans/zooms or switches altitude (`⌘1–5`).
- **User Journey:** Alex rests at Org (30k ft) → zooms to a Stream (3k ft) → drills to an Object (ground). Left = evidence/past, right = plans/future; outcomes flow right→left to the decisions that predicted them.
- **Screens Involved:** The Meridian canvas at all three altitudes.
- **System Actions:** Compose lens (GraphQL) → render graph state spatially; pan/zoom ≥60fps.
- **AI Agents Involved:** None directly (renders state).
- **APIs Invoked:** GraphQL canvas/lens queries (persisted, complexity-budgeted).
- **Database Entities Touched:** Hierarchy, decisions, outcomes, projections.
- **Success State:** Coherent spatial model; smooth altitude transitions.
- **Failure States:** Complexity-budget breach → query rejected/paginated; projection stale → freshness badge.
- **Notifications Generated:** None.
- **Audit Events Recorded:** None (reads).

---

## F-36 · Streams, Lenses & Brief Containers

- **User Goal:** (Sam) Organize work by responsibility (Streams), save/share views (Lenses), read generated narratives (Briefs) — without folders.
- **Entry Point:** Stream switcher; "Save this view as a Lens"; Brief.
- **Trigger:** User creates/curates a Stream, saves a Lens, or opens a Brief.
- **User Journey:** Sam curates a Stream (her area of responsibility) → arranges the canvas → saves it as a shareable Lens → shares with the team. Workspace = one company = one graph.
- **Screens Involved:** Stream view, Lens save/share dialog, Brief.
- **System Actions:** Persist Stream membership/curation; persist Lens config; render Brief ephemerally from ledger.
- **AI Agents Involved:** None (containers); Briefs are generated (F-32).
- **APIs Invoked:** REST for Stream/Lens commands; GraphQL for rendering.
- **Database Entities Touched:** `streams`, `lenses`; Briefs are not stored stale.
- **Success State:** Human curation captured; views shareable; no folder tree needed.
- **Failure States:** Lens references deleted objects → graceful degrade; sharing beyond ACL → trimmed.
- **Notifications Generated:** Lens shared → recipients notified.
- **Audit Events Recorded:** Stream/Lens create/share.

---

## F-37 · The Tide (Ranked Notifications) & Meridian Bar

- **User Goal:** (Anyone) Stay aware of what changed, interrupted only when it truly matters (Vermilion risk).
- **Entry Point:** The Tide (persistent ranked strip); the Meridian Bar (bottom, waypoints + time scrubber + altitude).
- **Trigger:** A finding/event is ranked into the Tide; Vermilion items interrupt.
- **User Journey:** Sam works → calm ranked items accrue in the Tide → a Vermilion contradiction interrupts → she opens it → acts. Time scrubber lets her replay state.
- **Screens Involved:** The Tide, Meridian Bar.
- **System Actions:** Rank events; gate interruption to Vermilion only; stream via SSE.
- **AI Agents Involved:** Sentinel (V2) is the engine behind most Tide items.
- **APIs Invoked:** SSE Tide stream (resumable, heartbeats).
- **Database Entities Touched:** `notifications`/`tide_items`, source findings.
- **Success State:** Calm awareness; only genuine risk interrupts.
- **Failure States:** SSE disconnect → resume via `Last-Event-ID`; over-interruption → ranking tuned.
- **Notifications Generated:** This *is* the notification surface.
- **Audit Events Recorded:** Tide item lineage to source runs.

---

## F-38 · Meridian Design System

- **User Goal:** (Anyone) Read evidence as a material — provenance visible, accessible, calm.
- **Entry Point:** Every surface (the design system is ambient).
- **Trigger:** Any render.
- **User Journey:** User reads any generated text → the Provenance Underline encodes evidence weight (thickness + glyph, color-redundant) → atmosphere switches Daylight↔Midnight by content mode (600ms crossfade) → motion is physics-based; AI "thinking" is a luminous pulse, never a spinner.
- **Screens Involved:** All.
- **System Actions:** Apply tokens (5 semantic hues, role-correct type, motion budget 350ms, `prefers-reduced-motion`); WCAG 2.2 AA in both atmospheres; per-reader i18n from one graph.
- **AI Agents Involved:** None (presentation).
- **APIs Invoked:** None (client rendering of `Claim[]`/provenance).
- **Database Entities Touched:** None directly.
- **Success State:** Provenance legible and accessible; calm authority.
- **Failure States:** Provenance unresolved → degraded underline class; reduced-motion → crossfade fallback.
- **Notifications Generated:** None.
- **Audit Events Recorded:** None.

---

## F-39 · Roadmap Agent

- **User Goal:** (Sam/Alex) Maintain a living plan — sequencing, capacity, dependencies, scenarios — traceable to evidence.
- **Entry Point:** Roadmap lens (Horizon); the Line ("re-sequence Q3 for the billing bet"); Conductor.
- **Trigger:** Roadmap task dispatched, or inputs change (capacity, new decision).
- **User Journey:** Sam opens Horizon → asks to re-sequence given a new decision → Roadmap agent proposes sequencing with dependencies + scenarios (cited) → Sam reviews/adjusts → **commit** (ledgered).
- **Screens Involved:** Horizon (roadmap lens), scenario view, Decision Sheet (on commit).
- **System Actions:** Retrieve constraints (capacity, dependencies, decisions, metrics) → sequence → render scenarios as `Claim[]` (simulated = violet).
- **AI Agents Involved:** Roadmap (A3); Prioritization (A4) for ranking; Conductor.
- **APIs Invoked:** `POST /api/v1/runs` (roadmap task); GraphQL Horizon lens; metric-store calls.
- **Database Entities Touched:** `roadmaps`, `releases`, dependencies, `decision_ledger`.
- **Success State:** Evidence-linked, dependency-aware plan; scenarios clearly hypothetical.
- **Failure States:** Capacity data missing → flagged; circular dependency → surfaced; scenario over-reach → violet-gated.
- **Notifications Generated:** Roadmap-change notify to affected owners.
- **Audit Events Recorded:** Roadmap run; any committed re-sequence.

---

## F-40 · Prioritization Agent

- **User Goal:** (Sam/Alex) Get defensible rankings with explicit trade-offs and counterfactuals — no invented inputs.
- **Entry Point:** The Arena (prioritization lens); the Line ("rank the Q3 candidates"); Conductor.
- **Trigger:** Prioritization task dispatched.
- **User Journey:** Sam opens the Arena → candidates ranked with trade-offs + counterfactuals, every input cited from the metric store (mandatory contrarian probe) → Sam adjusts weights → **commit** an Arena ranking (fires a commit ceremony → ledger).
- **Screens Involved:** The Arena, trade-off detail, Provenance Lens, commit.
- **System Actions:** Pull governed metrics (F-15) → rank → contrarian probe → render `Claim[]`; commit writes ledger entry.
- **AI Agents Involved:** Prioritization (A4); Conductor.
- **APIs Invoked:** `POST /api/v1/runs`; GraphQL Arena lens; metric-store calls; `POST /api/v1/decisions/commit` on commit.
- **Database Entities Touched:** Candidate set, `scoring_models`, `decision_ledger`, metric store.
- **Success State:** Defensible, cited ranking; theater eliminated.
- **Failure States:** Metric unavailable → "unmeasurable," never invented; tie/ambiguity → trade-off surfaced.
- **Notifications Generated:** Ranking committed → stakeholders notified.
- **Audit Events Recorded:** Prioritization run; committed ranking (ledger).
- **Human-approval checkpoint:** Commit ceremony on the ranking.

---

## F-41 · Compliance Baseline (SOC 2 Type II) & GDPR Erasure Cascade

- **User Goal:** (Olivia/security/data-subject) Meet SOC 2 and honor GDPR erasure ≤24h while keeping the audit chain intact.
- **Entry Point:** Admin → Compliance / Data Requests; or an inbound DSAR.
- **Trigger:** Security review, or a DSAR/erasure request.
- **User Journey:** Olivia receives a DSAR → submits erasure → cascade tombstones the subject's content ≤24h, leaving typed "redacted" stubs so ledger auditability survives.
- **Screens Involved:** Compliance dashboard, DSAR/erasure workflow.
- **System Actions:** Tombstone cascade; redacted stubs; soft-delete → purge path (F-03); audit chain preserved.
- **AI Agents Involved:** None.
- **APIs Invoked:** `POST /api/v1/dsar` (`202` + job); `GET /api/v1/audit/verify`.
- **Database Entities Touched:** Subject content rows, `tombstones`, ledger stubs.
- **Success State:** Erasure complete ≤24h; audit chain still verifiable.
- **Failure States:** Cascade misses a store → reconciliation; chain break risk → blocked (stubs preserve continuity).
- **Notifications Generated:** DSAR completion to Olivia + (as required) the data subject.
- **Audit Events Recorded:** Erasure executed (the action is audited; content is gone).
- **Security checkpoint:** Insider/over-privileged access (threat #4) + GDPR.

---

## F-42 · Resilience & Kill Switches

- **User Goal:** (Olivia/on-call) Halt a misbehaving agent/tool instantly and degrade honestly, never silently.
- **Entry Point:** Admin → Operations / Kill Switches; automatic on SLO breach.
- **Trigger:** Operator flips a kill switch (per tenant/agent/tool/level), or model-provider failover triggers.
- **User Journey:** On-call sees a misbehaving agent → flips the agent-level kill switch → runs halt immediately; Ask continues on a lower tier with a **visible quality badge**.
- **Screens Involved:** Operations console, kill-switch controls, status/quality badges.
- **System Actions:** Per-tenant/agent/tool/level kill switches; model-provider failover → lower tier + badge; in-cell multi-AZ HA (RTO 1h / RPO ≤5 min).
- **AI Agents Involved:** All (subject to kill switches).
- **APIs Invoked:** `POST /api/v1/ops/killswitch`; health/status reads.
- **Database Entities Touched:** Kill-switch state, run state.
- **Success State:** Immediate halt; honest degraded service continues.
- **Failure States:** Failover unavailable → Ask disabled with honest message (never silent); switch lag → escalation.
- **Notifications Generated:** Kill-switch + failover state to operators and (as quality badge) to users.
- **Audit Events Recorded:** Kill-switch activations, failover events.
- **Security checkpoint:** Containment control for a compromised/misbehaving agent.

---

---

# Part 2 — End-to-End Sequence Flows

Each flow shows the canonical chain — **User → Frontend → API Gateway (BFF) → Services → Database → AI Agents → Response** — then calls out 🔻 **Bottlenecks**, ❌ **Failure points**, ✅ **Human-approval checkpoints**, and 🔒 **Security checkpoints**. Performance budgets cited are the §13 CI-enforced limits.

---

## Flow 1 — Workspace Creation

**Goal:** An economic buyer/admin (Alex/Olivia) stands up a new company workspace = one graph.

```
User (Olivia)
  │ "Create workspace" (post-purchase / onboarding)
  ▼
Frontend (onboarding wizard)
  │ name, region/residency, initial admins
  ▼
API Gateway / BFF
  │ POST /api/v1/workspaces  (Idempotency-Key)   [auth: Clerk JWT, admin role]
  ▼
Services
  │ Tenancy service: allocate organization_id + workspace_id
  │ Provision RLS context; seed roles (RBAC×ABAC); register in cell/staging stamp
  │ Identity service (Clerk): bind org → IdP, enable SCIM, MFA policy for editor+
  │ Outbox: emit workspace.created
  ▼
Database (Postgres 16)
  │ INSERT organizations, workspaces (UUIDv7), role tables, outbox row (same tx)
  ▼
AI Agents
  │ None at creation. (Memory plane scopes initialized empty: org/product/user.)
  ▼
Response
  │ 201 + workspace resource → land Olivia in an empty-but-speaking Brief
  │ ("Connect your first source to begin.")
```

🔻 **Bottlenecks:** Cell/stamp provisioning if a dedicated cell is requested (enterprise, Year-2); Clerk org-binding round-trip. Year-1 shared-schema path is fast (single INSERT tx).
❌ **Failure points:** Idempotency conflict on double-submit (`409`); region/residency not available; Clerk org-bind failure → roll back workspace creation (outbox not emitted because same-tx).
✅ **Human-approval checkpoints:** None beyond the purchasing decision; admin role required.
🔒 **Security checkpoints:** (1) Clerk JWT + admin role; (2) `organization_id`/`workspace_id` stamped on the row → RLS boundary established from row zero; (3) residency region pinned for later cell placement.

---

## Flow 2 — Document Ingestion

**Goal:** Source content flows in safely and becomes typed, resolved, indexed graph state.

```
User (Olivia)
  │ Authorize a connector (Zendesk/Jira/Notion/Confluence/Slack/Linear)
  ▼
Frontend (Connectors settings)
  │ OAuth handoff (external) → grant read scope
  ▼
API Gateway / BFF
  │ POST /api/v1/connectors (Idempotency-Key); OAuth callback
  ▼
Services  ── INGESTION PIPELINE (three lanes: live ≤2m / standard ≤15m / bulk) ──
  │ F-08 Connector: store credential in SECRET STORE (never in PMOS tables);
  │                 register webhook/CDC; capture source ACL read_principals;
  │                 enqueue backfill (bulk lane)
  │        ▼
  │ F-09 Normalize → PII screen → INJECTION screen (quarantine:injection_suspect, inert)
  │        ▼
  │ F-10 Signal extraction (cheap-first cascade; ~70% never touch an LLM)
  │        → FeedbackAtoms / Commitments / DecisionCandidates / RiskSignals
  │        ▼
  │ F-11 Entity resolution → graph upsert WITH provenance + read_principals
  │        ▼
  │ F-12 Index fan-out → embeddings (Model Gateway) → Qdrant (point id = chunk id)
  ▼
Database
  │ raw_items, quarantine, feedback_atoms/commitments/decision_candidates/risk_signals,
  │ accounts, hierarchy, provenance, document_chunks (↔ Qdrant), outbox events per stage
  ▼
AI Agents
  │ Screening + extraction + ER models via Model Gateway (metered to agent_runs).
  │ No specialist agent yet; outputs feed Research (F-27) / Feedback Intelligence (F-22).
  ▼
Response
  │ Connector health = "healthy, backfilling"; live items reach the graph ≤2 min;
  │ findings begin surfacing in the Brief / Tide.
```

🔻 **Bottlenecks:** LLM-touch stage in extraction (mitigated by cheap-first cascade); embedding throughput at backfill scale (bulk lane, never blocks live); entity-resolution ambiguity. Lane discipline guarantees backfills never delay live contradiction detection.
❌ **Failure points:** OAuth scope insufficient / token expiry (re-auth prompt); webhook gap (CDC reconciliation); injection false-negative (caught later by structural separation + CI red-team); embedding failure (retry); Postgres↔Qdrant drift (nightly reconciler).
✅ **Human-approval checkpoints:** None in steady state; quarantine review by Olivia is optional triage, not a gate on clean content.
🔒 **Security checkpoints:** (1) Source-credential custody in secret store (threat #3); (2) **PII screen**; (3) **injection screen → inert quarantine** (threat #2, layer 1); (4) **source-ACL `read_principals` captured at upsert** (load-bearing for every later retrieval trim); (5) `organization_id` force-injected into the Qdrant filter from context, never request params.

---

## Flow 3 — Knowledge Search (Line "Go" mode)

**Goal:** Any user instantly navigates to an object/state. Budget: **Go <50ms**.

```
User (anyone)
  │ ⌘K → types "billing epic Q3"
  ▼
Frontend (the Line overlay)
  │ classify intent → "Go"
  ▼
API Gateway / BFF
  │ GET search (lexical+vector hybrid, ACL-trimmed)   [stream-ticket not needed; simple read]
  ▼
Services (retrieval service only path to Qdrant)
  │ Qdrant hybrid (dense+sparse, int8) + Postgres lookups; ACL trim pre-return
  ▼
Database / Index
  │ document_chunks ↔ Qdrant; hierarchy rows
  ▼
AI Agents
  │ None (Go is pure retrieval — no LLM in the hot path).
  ▼
Response
  │ Ranked results <50ms → user navigates to the object/altitude.
```

🔻 **Bottlenecks:** Qdrant query latency at 10⁸-chunk scale (int8 quantization + tiered pre-filter keep it <50ms); ACL trim cost. No LLM = no token-stream latency.
❌ **Failure points:** No result → honest empty state ("retrieval failed — try Ask"); index drift → reconciler; exact-match ID recall dip (golden set monitored; OpenSearch only if <0.95, TD-8).
✅ **Human-approval checkpoints:** None (read).
🔒 **Security checkpoints:** (1) Clerk JWT; (2) `organization_id` force-injected; (3) **source-ACL pre-fusion trim** — results the user can't see never appear.

---

## Flow 4 — Ask PMOS (Line "Ask" mode)

**Goal:** Grounded, cited, permission-correct answer. Budget: **Ask first token <700ms**; provenance resolve **<400ms**.

```
User (any viewer)
  │ ⌘K → "why don't we support SSO on Starter?"
  ▼
Frontend (the Line → streaming answer surface)
  │ classify intent → "Ask"; open SSE (single-use 60s stream ticket, never session token)
  ▼
API Gateway / BFF
  │ SSE Ask stream (Authorization via fetch-stream or stream-ticket for EventSource)
  ▼
Services  ── HYBRID GRAPHRAG (F-13) ──
  │ parallel: vector + lexical + typed-graph traversal + GOVERNED METRIC-STORE calls
  │           + ledger lookups
  │   → fusion → cross-encoder rerank → ACL-TRIMMED SELECT (pre-fusion)
  │   → claim-grounded generation (Model Gateway) → groundedness verification
  ▼
Database / Index
  │ document_chunks, hierarchy, decision_ledger, metric store, provenance
  ▼
AI Agents
  │ Retrieval-backed Ask; may invoke Research (A2). Numbers ONLY from metric-store tool calls.
  ▼
Response
  │ Claim[] streamed over SSE, each sentence with a Provenance Underline;
  │ click → Provenance Lens <400ms. If evidence unseen/insufficient →
  │ HONEST ABSTENTION ("n sources withheld by permissions" / "can't ground this").
```

🔻 **Bottlenecks:** First-token latency (frontier model via Gateway — batch lanes reserved for async, interactive prioritized); cross-encoder rerank; provenance resolve under 400ms at scale.
❌ **Failure points:** No groundable evidence → abstain (counts toward ≥95% honesty metric); model-provider outage → failover to lower tier + **visible quality badge**; SSE disconnect → resume via `Last-Event-ID`; metric unavailable → "unmeasurable," never fabricated.
✅ **Human-approval checkpoints:** None (read-only answer).
🔒 **Security checkpoints:** (1) **Stream-ticket auth** (never the session token over EventSource); (2) **pre-fusion ACL trim**; (3) honest existence-of-omission (never silently drops withheld sources); (4) numbers governed (no invented quantities).

---

## Flow 5 — Customer Feedback Analysis

**Goal:** Sam turns raw feedback into a quantified, account-linked, decision-ready cluster.

```
User (Sam)
  │ Opens Brief finding OR Line "show feedback on billing"
  ▼
Frontend (Brief → feedback cluster lens)
  │ GraphQL feedback lens (persisted query, complexity-budgeted)
  ▼
API Gateway / BFF
  │ GraphQL /api/graphql  + metric-store tool calls for quantification
  ▼
Services
  │ Feedback Intelligence (F-22): cluster FeedbackAtoms → quantify (account/revenue
  │   join via GOVERNED METRIC STORE) → thread to decisions/artifacts
  │ Research agent (A2) synthesizes on request
  ▼
Database
  │ feedback_atoms, accounts, insights, claims, metric store
  ▼
AI Agents
  │ Research (A2); Sentinel (V2) for risk threading. Contrarian probe on synthesis.
  ▼
Response
  │ Clustered, quantified, CITED feedback ("Billing friction 3× on enterprise, $1.2M ARR");
  │ Sam threads it to a decision (→ Flow 6) in one move.
```

🔻 **Bottlenecks:** Clustering over high-volume atoms; metric-store join latency; rerank for relevance.
❌ **Failure points:** Sparse signal → low-confidence clusters flagged `inference`; ACL trim removes atoms → "n withheld"; metric join unavailable → cluster shown unquantified but still cited.
✅ **Human-approval checkpoints:** None to *view*; threading to a decision leads into the commit ceremony (Flow 6).
🔒 **Security checkpoints:** (1) ACL-trimmed atoms; (2) revenue/account numbers from governed metric store only; (3) provenance on every claim.

---

## Flow 6 — PRD Generation

**Goal:** A committed decision becomes an evidence-native, build-ready PRD. **L2 human approval required.**

```
User (Sam)
  │ Decision Sheet → "Draft PRD"  (or Conductor delegation)
  ▼
Frontend (Decision Sheet → PRD review surface, serif artifact type)
  ▼
API Gateway / BFF
  │ POST /api/v1/runs  (PRD task) → 202 + job resource; SSE progress
  ▼
Services  ── AGENT RUNTIME (F-17) under POLICY ENGINE (F-18) ──
  │ Conductor plans → delegates to PRD agent (A5)
  │ PRD agent: retrieve (F-13) → claim-grounded generation
  │            → MANDATORY CONTRARIAN PROBE ("evidence against")
  │            → queue draft (L2, NOT yet written as final)
  ▼
Database
  │ agent_runs/agent_run_steps, documents (draft), claims, citations
  ▼
AI Agents
  │ PRD (A5) + Conductor (A1) + Research (A2). Writes carry ai_generated=true + source_run_id.
  ▼
Response (draft) → ✅ HUMAN REVIEW
  │ Sam edits non-goals (EDIT-DISTANCE LOGGED) → APPROVES
  │   → POST /api/v1/approvals/{runId} → policy engine issues capability token
  │   → PRD versioned (immutable version + chunks) → indexed → outbox event
```

🔻 **Bottlenecks:** Frontier-model generation latency; contrarian-probe second pass; eval-harness gate before surfacing; review latency is human (tracked as approval latency).
❌ **Failure points:** Insufficient evidence → sections marked `inference`; **eval gate fail → draft never surfaced**; approval declined → draft discarded/iterated; edit-distance >30% sustained → kill/pivot trigger (F-20).
✅ **Human-approval checkpoints:** **L2 approval before the PRD is committed** — the agent draft is *submitted to* the human (Design Law 5).
🔒 **Security checkpoints:** (1) Capability token issued only on approval (no token = no final write); (2) source content stays inside delimited typed evidence blocks (injection separation); (3) provenance on every sentence.

---

## Flow 7 — User Story Generation

**Goal:** An approved PRD becomes engineer-grade epics/stories/ACs. **L2 approval; the L3 push to Jira is V2 (Flow continues into Living Sync).**

```
User (Sam → Priya as reviewer)
  │ Approved PRD → "Generate stories"
  ▼
Frontend (story review surface, AC detail)
  ▼
API Gateway / BFF
  │ POST /api/v1/runs (story task) → 202; SSE progress
  ▼
Services  ── AGENT RUNTIME under POLICY ENGINE ──
  │ Story agent (A6): retrieve PRD + evidence → generate epic tree + stories + ACs as Claim[]
  │ EVAL HARNESS (F-20): measure normalized edit-distance + approval latency per (team, task-type)
  ▼
Database
  │ epics, user_stories, requirements, claims, edit_distance_records, agent_runs
  ▼
AI Agents
  │ Story Writing (A6) + PRD (A5) upstream + Conductor.
  ▼
Response (draft) → ✅ HUMAN REVIEW (Priya's bar)
  │ Sam/Priya review → APPROVE (L2) → stories finalized
  │   → (Year-1 stops here; V2 F-47 earns L3 push to Jira w/ diffs+rationale+revert handle → Flow into Living Sync)
```

🔻 **Bottlenecks:** Generation of a large epic tree; AC quality convergence; the human review itself (approval latency is the metric that gates Trust-Ladder promotion).
❌ **Failure points:** Edit-distance >30% after two quarters of tuning → **kill/pivot trigger fires (scope freeze, fix quality)**; eval gate fail → not surfaced; approval declined → iterate.
✅ **Human-approval checkpoints:** **L2 approval (Priya's engineer-grade quality bar)** — the central adoption risk for the whole program.
🔒 **Security checkpoints:** (1) Capability token on approval; (2) `ai_generated`/`source_run_id` on writes; (3) evidence-block separation.

---

## Flow 8 — Prioritization

**Goal:** Defensible ranking with trade-offs and counterfactuals, every input governed. Commit fires a ledger entry.

```
User (Sam/Alex)
  │ The Arena → "rank the Q3 candidates"
  ▼
Frontend (the Arena lens → trade-off detail)
  ▼
API Gateway / BFF
  │ POST /api/v1/runs (prioritization task) → 202; GraphQL Arena lens for render
  ▼
Services
  │ Prioritization agent (A4): pull GOVERNED METRICS (F-15) → rank → trade-offs
  │   → MANDATORY CONTRARIAN PROBE → counterfactuals (simulated = VIOLET) → Claim[]
  ▼
Database
  │ candidate set, scoring_models, metric store, claims
  ▼
AI Agents
  │ Prioritization (A4) + Conductor; counterfactuals lean on Strategist/Simulator (V2).
  ▼
Response → ✅ COMMIT CEREMONY
  │ Sam adjusts weights → commits the Arena ranking
  │   → POST /api/v1/decisions/commit (Idempotency-Key, signed, hash-chained)
  │   → decision_ledger entry (byproduct of the action, no separate form)
```

🔻 **Bottlenecks:** Metric-store calls per candidate; contrarian + counterfactual passes; ranking over large candidate sets.
❌ **Failure points:** Metric unavailable → "unmeasurable as predicted," never invented (ends prioritization theater); tie/ambiguity → trade-off surfaced; commit signature failure → rejected.
✅ **Human-approval checkpoints:** **The commit ceremony** — the human's typed initial is the judgment; the ledger entry is its byproduct.
🔒 **Security checkpoints:** (1) Numbers governed (no invented RICE/WSJF inputs); (2) signed, hash-chained ledger write; (3) simulated values gated to violet (never presented as real).

---

## Flow 9 — Roadmap Generation

**Goal:** A living, dependency-aware, evidence-linked plan with clearly-hypothetical scenarios.

```
User (Sam/Alex)
  │ Horizon lens → "re-sequence Q3 for the billing bet"
  ▼
Frontend (Horizon roadmap lens → scenario view)
  ▼
API Gateway / BFF
  │ POST /api/v1/runs (roadmap task) → 202; GraphQL Horizon lens
  ▼
Services
  │ Roadmap agent (A3): retrieve constraints (capacity, dependencies, decisions, metrics)
  │   → sequence → generate scenarios (Claim[]; simulated = VIOLET)
  │ Prioritization (A4) supplies ranking inputs
  ▼
Database
  │ roadmaps, releases, dependencies, decision_ledger, metric store
  ▼
AI Agents
  │ Roadmap (A3) + Prioritization (A4) + Conductor.
  ▼
Response → (optional) ✅ COMMIT CEREMONY
  │ Sam reviews sequencing + dependencies + scenarios → adjusts → commits the re-sequence
  │   → decision_ledger entry; affected owners notified
```

🔻 **Bottlenecks:** Dependency-graph computation; scenario generation over priors; capacity-data joins.
❌ **Failure points:** Capacity data missing → flagged, not guessed; circular dependency → surfaced for resolution; scenario over-reach → violet-gated as hypothesis.
✅ **Human-approval checkpoints:** Commit ceremony on any committed re-sequence (a roadmap change that alters decisions is ledgered).
🔒 **Security checkpoints:** (1) Metrics governed; (2) simulated scenarios visibly violet; (3) ledger write signed/hash-chained on commit.

---

## Flow 10 — Release Planning

**Goal:** Ship against readiness gates, notify promised accounts, and **arm outcome measurement** (closing the loop). *(Release Agent + Launch Control are V2/F-49; shown here as the loop-closing endpoint the V1 surfaces build toward.)*

```
User (Sam/Release owner)
  │ "Plan release" against an approved/synced epic tree
  ▼
Frontend (Launch Control)
  ▼
API Gateway / BFF
  │ POST /api/v1/runs (release task) → 202; SSE progress
  ▼
Services
  │ Release agent (A8): assess READINESS GATES → prepare rollout + comms (Claim[])
  │ On ship: notify PROMISED ACCOUNTS (via Commitment Ledger F-50) + ARM OUTCOME MEASUREMENT (F-48)
  │ Living Sync keeps spec↔tickets coherent (F-31 one-way / F-47 bidirectional)
  ▼
Database
  │ releases, sync_state, commitments, outcome measurement windows, decision_ledger
  ▼
AI Agents
  │ Release (A8) + Analytics (A7, arms measurement) + Conductor; Sentinel watches delivery risk.
  ▼
Response → ✅ READINESS APPROVAL (and L3 act-and-notify where earned)
  │ Human approves readiness → release proceeds → promised accounts notified
  │   → outcome measurement armed → (weeks later) Analytics closes window
  │      (+6.2% vs +8% predicted) → OutcomeReport → corpus → anti-pattern memory
  │      → scorecard updates everywhere it's quoted.
```

🔻 **Bottlenecks:** Readiness-gate evaluation; external comms; multi-system push if rollout touches several tools (true saga/rollback needs Temporal — V2/TD-4).
❌ **Failure points:** Readiness gate fails → release blocked; external push half-succeeds → compensating action surfaced (Year-1 has no automatic saga rollback — flagged for human); measurement not instrumentable → "unmeasurable as predicted," surfaced not hidden.
✅ **Human-approval checkpoints:** **Readiness approval** before ship; L3 act-and-notify only for earned task classes (V2 Trust Ladder GA).
🔒 **Security checkpoints:** (1) Capability tokens for every external write via the governed tool service (CI attack-success target 0); (2) promised-account notifications respect account ACLs; (3) outcome numbers governed and honestly bounded for CFO credibility.

---

## Cross-Flow Summary — Where the Four Control Types Concentrate

**Human-approval checkpoints (Year-1 caps autonomy at L1–L2):**
- Every artifact write (PRD F-24/Flow 6, Story F-25/Flow 7) is **L2 — drafted by the agent, approved by the human**.
- Every decision/ranking/roadmap change is committed via the **commit ceremony** (F-30/Flows 8–9) — the typed initial is the judgment; the ledger entry is its byproduct.
- Every external write (Living Sync F-31/Flow 7→Sync, Release Flow 10) requires **L2 approval before the governed tool service will execute**.
- The **eval harness (F-20)** is a release-blocking approval gate encoding Priya's quality veto.

**Security checkpoints (mapped to the ranked threat model):**
- **Cross-tenant exposure (threat #1, existential):** RLS + `organization_id` force-injection (Flows 1–5) + nightly cross-tenant probes (any hit = sev-0).
- **Prompt injection (threat #2):** ingestion screening → inert quarantine (Flow 2) → structural evidence-block separation (Flows 6–7) → tool-schema arg rejection → capability confinement (Flows 6,7,10). CI tool-call attack-success target = 0.
- **Source-credential theft (threat #3):** secret-store custody (Flow 2).
- **Insider/over-privileged (threat #4):** source-ACL pre-fusion trim on every read (Flows 3–5); RBAC×ABAC.
- **Audit tampering (threat #5):** hash-chained append-only ledger/autonomy/billing (Flows 6–10), WORM-anchored.

**The three highest-risk bottlenecks across all flows:**
1. **The ingestion → retrieval pipeline (Flow 2 → Flows 3–5):** six sequential high-complexity stages; the longest pole. Lane discipline protects live freshness; cheap-first cascade protects cost.
2. **Interactive generation latency (Flows 4, 6, 7):** frontier-model first-token under budget while batch lanes serve async work; the Model Gateway's tiering and failover (with honest quality badges) absorb provider variance.
3. **Generated-artifact quality convergence (Flows 6, 7):** not a latency problem but a *quality* one with a defined kill/pivot trigger (>30% edit-distance after two quarters). The eval harness must be mature early so tuning has runway.

---

*This document specifies Year-1-authoritative behavior per the spec's rule of precedence. Where a flow's endpoint is a V2 capability (Release/Launch Control, bidirectional Living Sync, synthetic Pre-Mortem), it is labeled so engineering can design the Year-1 seam without building the end-state prematurely. APIs, services, entities, agents, and checkpoints above are specified at the granularity needed to begin API and module design directly.*
