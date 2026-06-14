# PMOS — Feature Inventory

### From Vision to Build-Ready Engineering Backlog

**Source of truth:** `PMOS_MASTER_SPEC_Final.md` (Constitution v1.0)
**Purpose:** Convert the PMOS vision into a concrete, dependency-aware feature inventory that engineering teams can plan and implement against.
**Method:** Every feature implied by the specification — across Product Scope, Core Features, AI Capabilities, Agent Ecosystem, User Workflows, Information Architecture, and Future Roadmap — has been extracted, attributed, and tiered.

**A note on phasing.** The spec draws a hard line between *vision invariants* (cells, Kafka, Temporal, the generic bitemporal graph) and *Year-1 implementation* (shared-schema RLS, Redis Streams, NestJS orchestrator, relational hierarchy). This inventory builds against **Year-1 implementation as authoritative**, while naming the end-state each foundation feature must not foreclose. Tiers below map roughly to the spec's own sequencing: **Foundation** = the platform substrate that everything sits on (the §20 critical path), **MVP** = the Year-1 wedge and loop-closers, **V1** = remainder of Year-1 GA, **V2** = Year-2 ("AI Product Team Member"), **Future** = Year-3+ ("The Operating System").

---

## Legend

- **Complexity** — Low / Medium / High (engineering effort + technical risk).
- **Priority** — Critical (loop/platform cannot function without it) / Important (differentiating, but loop survives a phase without it) / Optional (upside, enterprise, or long-horizon).
- **AI Concepts Used** — the specific AI/ML machinery the feature depends on; "none" where the feature is pure platform.
- Feature IDs (`F-01`…) are stable references used by the dependency graph and build order at the end.

---

# 1. Foundation

*The platform substrate. Nothing in MVP/V1 can ship until these exist. This is the spec's §20 critical path expressed as discrete features.*

---

### F-01 · Multi-Tenancy & Row-Level Security (RLS) Core

- **Description:** Shared-database, shared-schema tenancy enforced by PostgreSQL Row-Level Security. `organization_id` **and** `workspace_id` on every content row; canonical RLS policy + `FORCE ROW LEVEL SECURITY`; per-request `SET LOCAL app.current_org_id` inside a tenant-scoped transaction (PgBouncer transaction-pooling safe). The data layer refuses any query lacking a `TenantContext`. Org boundary = hard RLS boundary; workspace = soft app-layer boundary (deliberate defense-in-depth split). Background workers run `BYPASSRLS` with mandatory code-reviewed explicit `organization_id` filtering.
- **User Problem Solved:** Customers will not trust a decision system of record that could leak their data to a competitor; cross-tenant exposure is the existential threat (threat #1).
- **Business Value:** Table-stakes for any enterprise sale; the mechanism that makes "unlimited free viewers" architecturally cheap; the design that keeps the eventual cell migration to event-replay + router-flip rather than a rewrite.
- **Dependencies:** PostgreSQL 16 provisioned. None other (this is bedrock).
- **AI Concepts Used:** None (platform).
- **Complexity:** High
- **Priority:** Critical
- **End-state not to foreclose:** Cell-based architecture (Year-2+); `organization_id`-on-every-row is the seam.

---

### F-02 · Identity & Access (Clerk Integration)

- **Description:** Clerk as canonical identity provider: SSO (SAML/OIDC), MFA for editor+ roles, SCIM (enterprise), JWKS-verified JWTs. Passwords never touch PMOS endpoints. Two principals modeled distinctly: a human's authority is a session claim (RBAC × ABAC × source-ACL trim); the `auth_provider` field is retained only as an identity-source label populated from Clerk, never a credential store.
- **User Problem Solved:** Enterprises require federated identity and refuse to hold credentials in yet another vendor; admins (Olivia) need role-correct access.
- **Business Value:** Unblocks enterprise procurement; eliminates a whole class of credential-theft risk (threat #3).
- **Dependencies:** F-01 (tenant context binds to identity).
- **AI Concepts Used:** None (platform).
- **Complexity:** Medium
- **Priority:** Critical

---

### F-03 · Core Persistence Schema & Product Hierarchy

- **Description:** PostgreSQL 16 as the system of record. Explicit first-class hierarchy tables: `Organization → Workspace → {Products → Features → Epics → User Stories → Requirements}`, plus Roadmaps/Releases, Feedback/Interviews/Insights, and Documents (immutable versions + chunks). UUIDv7 keys everywhere (time-ordered, doubles as Qdrant point ID). Conventions: `timestamptz` always, `updated_at` by trigger, soft delete (`deleted_at`, 30-day trash → purge = GDPR erasure path). Polymorphism `(context_type, context_id)` only for genuinely "to-anything" edges (comments, tags, links, conversations).
- **User Problem Solved:** PMs and agents both reason in product terms (product/feature/epic/story); a generic work-item table would force everything through awkward polymorphic routing.
- **Business Value:** The schema *is* the asset that compounds per tenant; clean keys/conventions reduce every downstream cost.
- **Dependencies:** F-01.
- **AI Concepts Used:** None (platform), but schema is AI-aware by design (see F-06).
- **Complexity:** High
- **Priority:** Critical
- **End-state not to foreclose:** Generic bitemporal typed graph layered over these tables (Year-2, TD-6).

---

### F-04 · Transactional Outbox & Event Backbone

- **Description:** Transactional outbox as the invariant — no event emitted without the state change committed in the same transaction. Redis Streams transport (Year-1) with a relay worker; explicitly Kafka/MSK-swappable seam for cell scale. Event fan-out drives projections and async work.
- **User Problem Solved:** Stale/inconsistent reads and lost work; "honest degradation, never silent staleness" is impossible without reliable eventing.
- **Business Value:** Makes projections rebuildable in minutes; decouples interactive writes from autonomous work so neither blocks the other.
- **Dependencies:** F-03, Redis provisioned.
- **AI Concepts Used:** None (platform).
- **Complexity:** Medium
- **Priority:** Critical
- **End-state not to foreclose:** Kafka/MSK at cell scale (TD-2).

---

### F-05 · API Gateway / BFF & Three-Protocol Contract

- **Description:** The BFF as the single client-facing surface — no client ever calls a domain service, Model Gateway, or datastore directly. Three-protocol rule: **REST** (`/api/v1`) for commands + simple reads, `Idempotency-Key` mandatory on every POST (24h response cache, body-hash mismatch → `409`); **GraphQL** (`/api/graphql`) for lenses, persisted queries only in production, complexity budget (cost ≤ 1,000, depth ≤ 8), DataLoader batching; **SSE** for streaming (Ask tokens/claims, agent-run progress, the Tide), resumable via `Last-Event-ID`, 15s heartbeats, stream-ticket auth. One async-job grammar (`202 Accepted` + job resource + SSE progress + cancel). Wire invariants: `Claim[]` protocol type, one error format, cursor-only pagination, two-key versioning (URI `/v1` + `PMOS-Version` date header). Ships with a built-in dev fixture implementing the exact contract.
- **User Problem Solved:** Without one disciplined surface, clients couple to internals and the system ossifies; teams can't develop in parallel without the fixture.
- **Business Value:** Enables the Year-3 public Platform/API without re-architecture; lets frontend and backend teams work against a stable contract from day one.
- **Dependencies:** F-01, F-03.
- **AI Concepts Used:** None directly (carries `Claim[]` payloads from AI features).
- **Complexity:** High
- **Priority:** Critical

---

### F-06 · AI Schema Spine (Agents, Runs, Conversations, Metering)

- **Description:** AI as first-class in the schema: `ai_agents` (with `model` binding), `conversations`, `messages` (with `tool_calls`), `agent_runs` (token-metered) + `agent_run_steps` (step trace). This is the audit & cost spine that every agent and metering feature reads from.
- **User Problem Solved:** Autonomy and consumption pricing are impossible to audit or bill without a per-run, per-step, token-metered record.
- **Business Value:** Foundation for consumption pricing (≥35% of revenue by Year-2) and for the "insurance-grade audit" sold in Year-3.
- **Dependencies:** F-03.
- **AI Concepts Used:** Token accounting; agent-run tracing.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-07 · Model Gateway

- **Description:** Single gateway fronting all frontier-model calls with ZDR contracts and no cross-tenant training by default. Per-task tiered routing (frontier / mid / small / embedding); default agent model `claude-sonnet-4-6` per `ai_agents.model`; OpenAI `text-embedding-3-large` @ 3072 dims for vectors. Routing policy hot-swappable per task type. Batch lanes for autonomous work; model-provider failover degrades Ask to a lower tier with a visible quality badge.
- **User Problem Solved:** "The model layer is rented; the learning loops are owned" — without a gateway, the org is locked to one provider and can't swap models or honor data-handling contracts.
- **Business Value:** A model upgrade improves PMOS overnight with zero migration; ZDR/failover are enterprise prerequisites.
- **Dependencies:** F-06 (model binding lives in `ai_agents`).
- **AI Concepts Used:** LLM routing/tiering; embeddings; ZDR; graceful degradation.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-08 · Connector SDK & Source Connectors (≥6)

- **Description:** A connector SDK plus six initial connectors covering modern mid-market stacks (Zendesk/Jira + Notion/Confluence as the value-proof minimum; Slack/Linear; Gong/Salesforce as honestly-surfaced coverage *upgrades*, never requirements). Connectors must expose webhooks/CDC and source ACL data for ≤2-min freshness and ≤1h ACL reconciliation.
- **User Problem Solved:** AI tool sprawl with zero compounding (problem 10) — PMOS must read the org's real signal sources, not start from zero.
- **Business Value:** The wedge's reach; coverage framed as upgrade ("+ Gong would raise customer-voice coverage 41% → 78%") drives expansion without lengthening the sales cycle.
- **Dependencies:** F-03, F-04.
- **AI Concepts Used:** None directly (feeds the enrichment pipeline).
- **Complexity:** High
- **Priority:** Critical

---

### F-09 · Ingestion Pipeline — Normalization, PII & Injection Screening

- **Description:** The Knowledge-plane front half: connectors → normalization → **PII screening + prompt-injection screening** (`quarantine:injection_suspect`, rendered inert) → ready for enrichment. Three ingestion priority lanes (live ≤2 min / standard ≤15 min / bulk best-effort); backfills never delay live detection. "Ingested content is hostile until proven otherwise" — all source content is data, never instructions.
- **User Problem Solved:** Prompt injection driving unauthorized action (threat #2, the AI-native attack); PII leakage.
- **Business Value:** First layer of the injection defense-in-depth that lets PMOS safely ingest hostile content at scale; lane prioritization keeps live contradiction detection fast and cheap.
- **Dependencies:** F-08, F-04.
- **AI Concepts Used:** PII detection; prompt-injection classification; content sanitization.
- **Complexity:** High
- **Priority:** Critical

---

### F-10 · Signal Extraction & Enrichment (the "crown jewel")

- **Description:** Turn raw normalized content into typed objects: a ticket → typed `FeedbackAtom`s, a call → `Commitment`s, a Slack thread → `DecisionCandidate`s, plus `RiskSignal`s. Cheap-first cascades so ~70% of records never touch an LLM.
- **User Problem Solved:** The synthesis ceiling (problem 1) — feedback grew ~10× while PM headcount is flat.
- **Business Value:** Named the crown jewel; it is what converts undifferentiated text into the structured graph that everything downstream reasons over.
- **Dependencies:** F-09.
- **AI Concepts Used:** LLM information extraction; typed entity/atom extraction; cost-tiered model cascade.
- **Complexity:** High
- **Priority:** Critical

---

### F-11 · Entity Resolution & Graph Upsert

- **Description:** Resolve extracted atoms to canonical entities (e.g. a `FeedbackAtom` entity-resolved to an `Account`) and upsert into the product hierarchy *with provenance*. Source-ACL `read_principals` carried onto every chunk/node at upsert time.
- **User Problem Solved:** Feedback black hole (problem 6) — signal must attach to the right account/feature to ever be findable or actionable.
- **Business Value:** Provenance + ACL at write time is what makes every later read both citable and permission-safe; ties signal to accounts/revenue.
- **Dependencies:** F-10, F-03.
- **AI Concepts Used:** Entity resolution / record linkage; provenance modeling.
- **Complexity:** High
- **Priority:** Critical

---

### F-12 · Vector & Lexical Index Fan-Out (Qdrant)

- **Description:** Index fan-out from Postgres to Qdrant. Vectors are **derived and always rebuildable** from Postgres — nothing stored in Qdrant that can't be regenerated. The `document_chunks` table is the Postgres↔Qdrant contract: chunk `id` *is* the Qdrant point ID; `content_hash` prevents redundant re-embedding; nightly reconciler heals drift. Qdrant payload-partitioned single-collection-per-granularity, `is_tenant`-indexed `organization_id` force-injected from context; reachable only from the retrieval service. Native sparse+dense hybrid (no separate lexical engine Year-1). Int8 quantization. Collections dimension-fixed (blue/green on model change).
- **User Problem Solved:** Retrieval at scale with exact-match/ID recall and tenant isolation; "the graph is the system; retrieval is a view of it."
- **Business Value:** Cheap, rebuildable index; tenant-safe by force-injected filter; defers OpenSearch cost (TD-8).
- **Dependencies:** F-11, F-07 (embeddings), Qdrant provisioned.
- **AI Concepts Used:** Dense + sparse embeddings; hybrid vector search; quantization.
- **Complexity:** High
- **Priority:** Critical
- **End-state not to foreclose:** OpenSearch BM25 leg only if exact-match recall drops below ~0.95 on the largest tenant (TD-8).

---

### F-13 · Hybrid GraphRAG Retrieval v1

- **Description:** The Retrieval plane: parallel vector + lexical + typed-graph traversal + governed metric-store tool calls + ledger lookups → fusion → rerank → **ACL-trimmed select (pre-fusion)** → claim-grounded generation → groundedness verification. Tiered search (coarse pre-filter → rescore → cross-encoder → LLM-select). **Numbers are tools, not text** — any quantitative claim must originate from a governed metric-store call. Honest abstention: an answer computed without evidence the asker can see renders "n sources withheld by permissions."
- **User Problem Solved:** Institutional amnesia (problem 5) and the "AI slop" credibility gap — retrieval must be grounded, permission-safe, and honest about what it can't see.
- **Business Value:** The engine behind every cited surface; groundedness verification is what clears P4's veto bar.
- **Dependencies:** F-12, F-11, F-07, F-15 (metric store for numeric tool calls).
- **AI Concepts Used:** RAG, GraphRAG, hybrid fusion, cross-encoder reranking, LLM-select, groundedness/faithfulness verification, tool-use for numeric grounding.
- **Complexity:** High
- **Priority:** Critical

---

### F-14 · Claim[] Protocol & Provenance Substrate

- **Description:** Every AI-generated prose field anywhere in the system is a `Claim[]` (`{text, citations[], kind, confidence}`) at the wire level — not a convention, the protocol. Uncitable claims marked `inference` and rendered differently. Provenance is resolvable in <400ms. Redundant non-color encoding (the Provenance Underline: thickness + glyph) for accessibility.
- **User Problem Solved:** "Every generated sentence is a contract" — no uncited prose; answers P4's AI-slop veto and Eddie's need for one trustworthy narrative.
- **Business Value:** The single most differentiating invariant; structurally unavailable to document-generators. Provenance-as-material is the product's signature.
- **Dependencies:** F-13 (generation produces Claims), F-05 (`Claim[]` is a wire invariant).
- **AI Concepts Used:** Citation/grounding, confidence scoring, claim typing (fact vs. inference).
- **Complexity:** Medium
- **Priority:** Critical

---

### F-15 · Governed Metric Store

- **Description:** A governed store of quantitative facts that AI features call as tools — the only legitimate origin for any number in any generated claim ("numbers are tools, not text"). Inspectable join logic for outcome attribution credibility with a CFO.
- **User Problem Solved:** Prioritization theater (problem 4) and outcome-attribution credibility — invented numbers destroy trust with a CFO.
- **Business Value:** Makes every quantitative claim defensible and inspectable; prerequisite for the SLA'd outcome tier.
- **Dependencies:** F-03.
- **AI Concepts Used:** Tool-use grounding; metric governance.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-16 · Memory Plane (Four Cognitive Types × Three Scopes)

- **Description:** Four cognitive memory types — **working** (task workspace), **episodic** (Decision Ledger + run logs, permanent), **semantic** (the graph/indexes), **procedural** (templates, scoring models, anti-patterns, human-governed) — across three scopes: organizational, product, user. Each agent carries a memory lens.
- **User Problem Solved:** AI tools that start from zero context every time (problem 10); the "earn memory" first step of the guiding sequence.
- **Business Value:** The compounding moat — memory accrues per tenant; a competitor on the same model is years behind on a tenured tenant.
- **Dependencies:** F-03, F-12, F-06.
- **AI Concepts Used:** Agent memory architectures; retrieval-scoped context; procedural memory / anti-pattern stores.
- **Complexity:** High
- **Priority:** Critical

---

### F-17 · Agent Runtime (Stateless, Checkpointed, Replayable Runs)

- **Description:** The orchestration substrate: a NestJS orchestrator + BullMQ executing stateless, checkpointed, replayable, capability-gated runs. All state lives in the Task Workspace and memory plane; agents are stateless between tasks, so every run is replayable and auditable. Runs are kept stateless/checkpointed so the eventual Temporal swap is mechanical.
- **User Problem Solved:** Autonomy that can't be audited or replayed is untrustworthy; "autonomy is enforced in the runtime, never in the prompt."
- **Business Value:** The execution substrate for all eleven agents; replayability is the audit guarantee sold to enterprises.
- **Dependencies:** F-06, F-07, F-04.
- **AI Concepts Used:** Agent orchestration; tool-use loops; run checkpointing/replay.
- **Complexity:** High
- **Priority:** Critical
- **End-state not to foreclose:** Temporal durable engine (Year-2, TD-4).

---

### F-18 · Policy Engine v1 & Capability Tokens (L0–L2)

- **Description:** The autonomy contract enforced in the runtime. Authority is a consumable, audited capability token bound to (run, task-class, approval event, 5-min TTL), issued by the policy engine, verified cryptographically at the tool service. An agent whose session lacks a token cannot exercise the capability. Year-1 ships the L0–L2 token machinery; L2 artifacts require human approval endpoints; agent writes carry `ai_generated=true` + `source_run_id`.
- **User Problem Solved:** "Enterprises won't grant autonomy" — autonomy must be graduated, earned, and revocable, never defaulted.
- **Business Value:** Monetizes at L1–L2 immediately; the structural guarantee against prompt injection driving unauthorized action (CI attack-success-rate target 0 at the tool-call layer).
- **Dependencies:** F-17, F-02.
- **AI Concepts Used:** Capability confinement for agents; tool-call authorization.
- **Complexity:** High
- **Priority:** Critical
- **End-state not to foreclose:** Full L3/L4 token TTL + two-person-rule at Trust Ladder GA (Year-2, TD-3).

---

### F-19 · Governed Tool Service

- **Description:** The service that executes agent tool calls, verifying capability tokens cryptographically and rejecting evidence-sourced arguments for sensitive parameters (part of injection defense-in-depth). Tool schemas are the contract; the CI red-team targets a tool-call attack-success-rate of 0.
- **User Problem Solved:** Prompt injection (threat #2) at the point of action; the tool layer is where an unauthorized action would actually fire.
- **Business Value:** The enforcement point that makes graduated autonomy safe to sell.
- **Dependencies:** F-18, F-17.
- **AI Concepts Used:** Tool-use; structural prompt separation; capability verification.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-20 · Eval Harness (Release-Gating)

- **Description:** The evaluation harness that gates releases — explicitly "built second, not last." Measures normalized edit-distance on accepted stories, time-to-approval per (team, task-type), groundedness, and the honesty/abstention metric (≥95% correct abstention on unanswerable sets). Encodes the kill/pivot trigger (>30% edit-distance after two quarters).
- **User Problem Solved:** Generated-artifact quality below P4's bar (the central adoption risk); quality regressions shipping silently.
- **Business Value:** The instrument that converts engineering leads from veto to advocate; edit-distance is itself a Trust-Ladder promotion gate.
- **Dependencies:** F-17, F-13. (Must precede broad agent rollout.)
- **AI Concepts Used:** LLM evaluation; edit-distance metrics; groundedness/abstention scoring; eval-driven gating.
- **Complexity:** High
- **Priority:** Critical

---

### F-21 · Audit Fabric (Append-Only Hash-Chained Ledger Substrate)

- **Description:** One audit pattern for all auditable surfaces: append-only Postgres tables, each row `row_hash = H(prev_hash ‖ canonical_payload)`, hourly chain-head anchors to WORM S3, full OTel trace linkage. `UPDATE`/`DELETE` revoked at the role level. Underpins the Decision Ledger, autonomy log, and billing meters.
- **User Problem Solved:** Audit-record tampering (threat #5); institutional amnesia where decision rationale is stored nowhere.
- **Business Value:** Deliberately the same machinery sold as "insurance-grade audit" in Year-3 and inspected by security reviews; "append-only for anything auditable" is a product principle.
- **Dependencies:** F-03, S3/WORM storage.
- **AI Concepts Used:** None (platform).
- **Complexity:** Medium
- **Priority:** Critical

---

# 2. MVP

*The Year-1 wedge and the minimum closed loop that proves 30-day ROI. These deliver the "Product Org's Memory" exit goal (25–40 mid-market logos).*

---

### F-22 · Feedback Intelligence (the wedge)

- **Description:** Every signal clustered, quantified, tied to accounts/revenue, and threaded to the decisions it should inform. The primary value-proof surface.
- **User Problem Solved:** Synthesis ceiling (problem 1) and feedback black hole (problem 6).
- **Business Value:** The wedge; must prove ≥5–6 hrs/PM/week recovered within 30 days on the lowest-trust connector set (tickets+docs alone).
- **Dependencies:** F-10, F-11, F-13, F-15.
- **AI Concepts Used:** Clustering, feedback synthesis, quantification, retrieval.
- **Complexity:** High
- **Priority:** Critical

---

### F-23 · The Free Diagnostic (GTM)

- **Description:** A free, self-serve diagnostic run as the go-to-market wedge — ingests a prospect's accessible sources and surfaces findings to prove value before purchase. Implemented on the standard async-job grammar.
- **User Problem Solved:** Buyers (Alex) can't see what they're missing; the diagnostic makes the synthesis gap visible and self-evident.
- **Business Value:** The top-of-funnel GTM mechanism for Year-1 logo acquisition; demonstrates the wedge on the prospect's own data.
- **Dependencies:** F-22, F-08, F-09.
- **AI Concepts Used:** Feedback synthesis; coverage estimation.
- **Complexity:** Medium
- **Priority:** Important

---

### F-24 · Artifact Engine — Evidence-Native PRD (PRD Agent, L1/L2)

- **Description:** PRD generation where every sentence traces to sources (`Claim[]`). Decision → build-ready evidence-native spec. Mandatory contrarian probe ("evidence against") to fight confirmation bias structurally. L1 draft / L2 act-with-approval; human approval endpoint; `ai_generated=true` + `source_run_id` on writes; edit-distance logged.
- **User Problem Solved:** Document graveyards/drift (problem 3) and the "AI slop" veto (P4); status-report tax (problem 2).
- **Business Value:** Evidence-native generation is the answer to P4's quality bar; edit-distance is a Trust-Ladder gate.
- **Dependencies:** F-13, F-14, F-17, F-18, F-16, F-20.
- **AI Concepts Used:** Claim-grounded generation; contrarian/red-team probe; retrieval; edit-distance feedback.
- **Complexity:** High
- **Priority:** Critical

---

### F-25 · Artifact Engine — Story Writing (Story Agent, L1/L2)

- **Description:** Spec → epics/stories/ACs at engineer-grade quality, generated as evidence-native `Claim[]`. L1/L2 in Year-1 (the L3 push to Jira is V2). Edit-distance and approval latency measured per (team, task-type).
- **User Problem Solved:** Inconsistent craft at scale (problem 9); the engineer-approved quality bar (P4).
- **Business Value:** Where the edit-distance kill/pivot trigger (>30% after two quarters) is measured; converting P4 from veto to advocate is the gating risk for the whole roadmap.
- **Dependencies:** F-24, F-13, F-20.
- **AI Concepts Used:** Claim-grounded generation; engineer-grade AC synthesis; eval-gated quality.
- **Complexity:** High
- **Priority:** Critical

---

### F-26 · Conductor Agent (AI Chief of Staff)

- **Description:** System agent S1: intake, planning, delegation to specialists, assembly of results, and submission to humans. The orchestration brain of the agent ecosystem.
- **User Problem Solved:** Coordinating multi-agent work without a human manually routing tasks; the "every day starts at review and decide" promise.
- **Business Value:** The control plane that makes the specialist agents usable as a team rather than a toolbox.
- **Dependencies:** F-17, F-18, F-16.
- **AI Concepts Used:** Agent planning/decomposition; delegation; multi-agent orchestration.
- **Complexity:** High
- **Priority:** Critical

---

### F-27 · Research Agent

- **Description:** Specialist A2: evidence work — feedback synthesis, interviews, market context. The agent face of Feedback Intelligence.
- **User Problem Solved:** Synthesis ceiling (problem 1); turning raw signal into decision-ready evidence.
- **Business Value:** Powers the wedge as an agent; recovers PM hours.
- **Dependencies:** F-22, F-13, F-17.
- **AI Concepts Used:** Feedback synthesis; retrieval; summarization with citations.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-28 · Decision Ledger v1

- **Description:** Every decision a first-class, versioned object — options, evidence, assumptions, predicted impact, owner, dissent, review date ("git for product decisions"). v1 is the relational realization; entries are a *byproduct of actions people already take* (no standalone "log a decision" form). Hash-chained via the audit fabric.
- **User Problem Solved:** Institutional amnesia (problem 5) — decision rationale stored nowhere; orgs relitigate and repeat failures.
- **Business Value:** A first-class object structurally unavailable to competitors; "decisions are the product, documents are exhaust."
- **Dependencies:** F-21, F-03, F-30 (commit ceremony populates it).
- **AI Concepts Used:** None core (capture is byproduct); enriched later by the Archivist (V2).
- **Complexity:** Medium
- **Priority:** Critical
- **End-state not to foreclose:** Full generic bitemporal hash-chained Decision Ledger layered over relational tables (Year-2, TD-6).

---

### F-29 · Ask-the-Brain v1 (Org-Wide Product Brain)

- **Description:** Anyone asks "why don't we support SSO on Starter?" and gets decision + evidence + owner + review date — claim-grounded, ACL-trimmed, with honest abstention. Token/claim streaming over SSE; Ask first token <700ms.
- **User Problem Solved:** Institutional amnesia (problem 5); the whole company (free viewers) needs one trustworthy place to ask "why."
- **Business Value:** Ubiquity is the expansion engine — free unlimited viewers using Ask is how PMOS spreads org-wide.
- **Dependencies:** F-13, F-14, F-28.
- **AI Concepts Used:** Claim-grounded QA; retrieval; honest abstention; streaming generation.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-30 · Commit Ceremony & Decision Sheet

- **Description:** The ceremonial write surface where a human commits a decision (typed initial → hash-chained ledger entry). The Decision Sheet presents The Question and The Call; supports running a Pre-Mortem and adding guards (e.g. "gate at 5% until assumption A3 verifies"). Interactive write path: BFF → domain service → Postgres tx (state + outbox) + hash-chain append + signature verification.
- **User Problem Solved:** Decisions made in people's heads or chat with no record (problems 5, 6); "ceremony only where it matters."
- **Business Value:** The action that fills the ledger organically (>40% of committed decisions should originate as a byproduct of an existing action).
- **Dependencies:** F-28, F-21, F-05.
- **AI Concepts Used:** None core; Pre-Mortem uses synthetic stakeholders (see F-44).
- **Complexity:** Medium
- **Priority:** Critical

---

### F-31 · Living Sync v1 (One-Way Spec → Jira/Linear/ADO)

- **Description:** One-way sync pushing the spec layer to execution tools with diffs + rationale; the foundation for bidirectional drift detection. PMOS syncs, never replaces, the execution tool.
- **User Problem Solved:** Document graveyards/drift (problem 3) — PRDs diverge from tickets within days.
- **Business Value:** Keeps the spec and execution coherent; respects the non-goal of not ripping out Jira/Linear.
- **Dependencies:** F-08, F-19, F-24/F-25 (produces the artifacts to sync).
- **AI Concepts Used:** Diff/rationale generation.
- **Complexity:** Medium
- **Priority:** Important
- **End-state not to foreclose:** Bidirectional Living Sync with revert handles (V2, F-39).

---

### F-32 · The Brief / Standing Brief v1 + Notify

- **Description:** Continuously-current narrative re-rendered from the ledger (never stored stale), published by local 6am ≥99.5% of days. "The system speaks first" — leads with a finding (what changed, what it means, what's recommended). Every claim provenance-linked. Plus baseline notify.
- **User Problem Solved:** Status-report tax (problem 2) — 30–50% of PM time on artifacts stale on arrival.
- **Business Value:** Half of the provable wedge ROI (feedback intelligence + auto-reporting = ≥5–6 hrs/PM/week).
- **Dependencies:** F-28, F-14, F-13, F-04 (projections).
- **AI Concepts Used:** Claim-grounded narrative generation; finding ranking.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-33 · The Line (Command Interface) & Search v1

- **Description:** The single command interface (`⌘K`, three blended modes — Go / Ask / Do). Go <50ms; Ask streams claims; Do triggers agent actions. The IA deliberately omits folders/page-trees/global lists — if a user wants to "organize," they ask the Line.
- **User Problem Solved:** Navigation and retrieval as the *only* organizing mechanism; eliminates document-graveyard browsing.
- **Business Value:** The signature interaction; 100% pointer-free doctrine; the entry point to every capability.
- **Dependencies:** F-13 (Ask), F-29, F-05.
- **AI Concepts Used:** Intent classification (Go/Ask/Do routing); retrieval.
- **Complexity:** Medium
- **Priority:** Critical

---

### F-34 · Consumption Metering & Billing Meters

- **Description:** Per-autonomy-unit metering built on `agent_runs` token accounting; append-only billing meters via the audit fabric. Exposes both a predictable platform fee and metered autonomy units side by side.
- **User Problem Solved:** PM orgs' uncertainty about metered AI pricing (Q4) — let the first cohort choose the mix.
- **Business Value:** Consumption ≥35% of revenue by Year-2 depends on this existing from day one; metering every unit costs nothing to A/B the pricing model.
- **Dependencies:** F-06, F-21.
- **AI Concepts Used:** None (reads token meters).
- **Complexity:** Medium
- **Priority:** Important

---

# 3. V1

*Remainder of Year-1 GA — the canvas, altitudes, and design-system surfaces that make the loop usable end-to-end, plus the early specialist agents.*

---

### F-35 · The Meridian Canvas & Altitudes

- **Description:** One canvas, many lenses. The Meridian: one horizontal spatial axis (left = evidence/past, right = plans/future; outcomes flow right→left to attach to the decisions that predicted them). Three altitudes: Org (30k ft) · Stream (3k ft) · Object (ground). "One surface, many lenses; never many apps in a trenchcoat." Canvas pan/zoom ≥60fps.
- **User Problem Solved:** App/tool sprawl and the dashboard-of-widgets; gives every persona a single spatial model of the org's state.
- **Business Value:** The product's structural differentiation from "many apps in a trenchcoat"; the surface every lens projects onto.
- **Dependencies:** F-05 (GraphQL lenses), F-03.
- **AI Concepts Used:** None directly (renders graph state).
- **Complexity:** High
- **Priority:** Important

---

### F-36 · Streams, Lenses & Brief Containers

- **Description:** The container model: **Stream** (the only human-curated container — a durable area of responsibility), **Lens** (a saved, shareable canvas configuration), **Brief** (generated ephemeral narrative). Workspace = one company = one graph.
- **User Problem Solved:** Organizing without folders; PMs (Sam) need a working altitude tied to their responsibility.
- **Business Value:** Streams are the human curation layer that makes the graph navigable; lenses make views shareable.
- **Dependencies:** F-35, F-03.
- **AI Concepts Used:** None (platform).
- **Complexity:** Medium
- **Priority:** Important

---

### F-37 · The Tide (Ranked Notifications) & Meridian Bar

- **Description:** The Tide — calm, ranked notifications that interrupt only for Vermilion (contradiction/risk). The Meridian Bar — bottom strip with waypoints (`⌘1–5`), time scrubber, altitude control. Delivered over SSE.
- **User Problem Solved:** Notification overload; the user must always be able to answer "what changed and how urgent."
- **Business Value:** Calm authority as a UX promise; the surface where Sentinel findings reach the human.
- **Dependencies:** F-05 (SSE), F-43 (Sentinel feeds it) for full value; ranking can ship with MVP signals first.
- **AI Concepts Used:** Finding ranking/prioritization.
- **Complexity:** Medium
- **Priority:** Important

---

### F-38 · Meridian Design System

- **Description:** The full design system: two atmospheres (Daylight/Midnight, 600ms crossfade), exactly five semantic hues used only when meaningful, role-correct typography (sans chrome / serif artifacts / mono IDs), the **Provenance Underline** (thickness + glyph encoding evidence weight, accessible), physics-based motion (one spring curve, luminous-pulse "thinking," 350ms budget, `prefers-reduced-motion` support), WCAG 2.2 AA in both atmospheres, per-reader i18n from one graph.
- **User Problem Solved:** Trust and legibility — evidence must be visible as a material; accessibility is non-negotiable.
- **Business Value:** "UI polish is table stakes"; the Provenance Underline is the visual signature of the cited-everything promise.
- **Dependencies:** F-14 (provenance to render), F-35.
- **AI Concepts Used:** None (presentation).
- **Complexity:** Medium
- **Priority:** Important

---

### F-39 · Roadmap Agent

- **Description:** Specialist A3: the living plan — sequencing, capacity, dependencies, scenarios.
- **User Problem Solved:** Roadmap items untraceable to evidence (problem 4); plans that drift from reality.
- **Business Value:** Turns the roadmap into a queryable, evidence-linked artifact rather than a static slide.
- **Dependencies:** F-17, F-13, F-28.
- **AI Concepts Used:** Sequencing/scheduling reasoning; scenario generation.
- **Complexity:** Medium
- **Priority:** Important

---

### F-40 · Prioritization Agent

- **Description:** Specialist A4: defensible ranking, trade-offs, counterfactuals. Mandatory contrarian probe. Inputs grounded in the metric store, never invented.
- **User Problem Solved:** Prioritization theater (problem 4) — RICE/WSJF inputs invented.
- **Business Value:** Defensible, evidence-traceable prioritization is a direct kill of the theater problem.
- **Dependencies:** F-13, F-15, F-17.
- **AI Concepts Used:** Ranking/scoring; contrarian probe; counterfactual reasoning.
- **Complexity:** Medium
- **Priority:** Important

---

### F-41 · Compliance Baseline (SOC 2 Type II) & GDPR Erasure Cascade

- **Description:** SOC 2 Type II posture; GDPR DSAR/erasure cascade ≤24h via tombstones leaving typed "redacted" stubs so ledger auditability survives content removal. (ISO 27001 and BYOK are later.)
- **User Problem Solved:** Insider/over-privileged access (threat #4) and regulatory requirements that gate mid-market+ deals.
- **Business Value:** Unblocks deals that require SOC 2; erasure-with-audit-survival is a non-trivial differentiator.
- **Dependencies:** F-01, F-02, F-21, F-03 (soft-delete path).
- **AI Concepts Used:** None (platform/compliance).
- **Complexity:** Medium
- **Priority:** Important

---

### F-42 · Resilience & Kill Switches

- **Description:** In-cell multi-AZ HA; RTO 1h / RPO ≤5 min; model-provider failover degrading Ask to a lower tier with a visible quality badge (honest degradation); kill switches per tenant / agent / tool / autonomy-level.
- **User Problem Solved:** Honest degradation never silent staleness (principle 8); operators need to halt a misbehaving agent instantly.
- **Business Value:** Interactive availability 99.9% SLO; per-level kill switches are a prerequisite for granting any autonomy.
- **Dependencies:** F-07, F-17, F-18.
- **AI Concepts Used:** Graceful model failover.
- **Complexity:** Medium
- **Priority:** Important

---

# 4. V2

*Year-2 — "The AI Product Team Member." Trust Ladder GA, the standing autonomous shift, and the cross-silo intelligence features. Cells, BYOK, and Temporal land in this window.*

---

### F-43 · Sentinel Agent + Contradiction Engine / Strategy Drift Detector

- **Description:** System agent S2 standing watch for contradictions, drift, risk, and promise radar — the engine behind the Tide. Surfaces cross-silo conflict (e.g. rising churn on an account whose audit-log promise is slipping) as Now-tier Tide items, and detects stated strategy vs. actual resource allocation.
- **User Problem Solved:** Risk discovered only in post-mortems (problem 8); cross-silo contradictions invisible until they explode.
- **Business Value:** A continuously-watching object competitors can't retrofit; turns risk from reactive to proactive.
- **Dependencies:** F-13, F-16, F-17, F-37 (Tide surface).
- **AI Concepts Used:** Contradiction/conflict detection; anomaly detection; cross-document reasoning.
- **Complexity:** High
- **Priority:** Important

---

### F-44 · Trust Ladder GA (L0–L3, Audit & Rollback)

- **Description:** Full graduated-autonomy GA: L0 observe → L1 draft → L2 act-with-approval → L3 act-and-notify. Promotion earned by measured accuracy and granted by humans (a signed ledger decision); automatic logged demotion on bar breach. Full capability-token TTL and two-person-rule; arbitrary-checkpoint run replay for audit disputes.
- **User Problem Solved:** "No autonomy before trust" — the mechanism that lets earned autonomy expand safely.
- **Business Value:** ≥40% of artifact volume at L2+ by Year-2; autonomy is the upside the consumption model monetizes.
- **Dependencies:** F-18, F-20 (edit-distance gates promotion), F-21 (signed ledger decisions), F-17 (replay).
- **AI Concepts Used:** Accuracy-gated autonomy promotion; capability tokens; audited rollback.
- **Complexity:** High
- **Priority:** Important

---

### F-45 · The Overnight PM

- **Description:** A standing autonomous shift that triages, refreshes, drafts, and queues overnight, so every day starts at "review and decide." Runs on batch lanes of the Model Gateway, never user-blocking.
- **User Problem Solved:** Status-report tax + synthesis ceiling — the PM should wake to decisions, not a backlog of synthesis.
- **Business Value:** The most visible expression of "PMOS does the product management"; a headline Year-2 capability.
- **Dependencies:** F-44, F-26, F-24, F-25, F-43.
- **AI Concepts Used:** Autonomous multi-agent scheduling; triage; draft generation.
- **Complexity:** High
- **Priority:** Important

---

### F-46 · Archivist Agent

- **Description:** System agent S3: memory steward — decision capture from the wild (proposes `DecisionCandidate`s mined from Slack/meetings so the ledger fills itself), consolidation, and provenance repair.
- **User Problem Solved:** Decision-ledger adoption lag (Q3) — the ledger must fill itself rather than depend on humans logging decisions.
- **Business Value:** Drives organic ledger adoption (>40% byproduct target); maintains memory quality over time.
- **Dependencies:** F-28, F-10, F-16, F-17.
- **AI Concepts Used:** Decision mining/extraction; entity consolidation; provenance repair.
- **Complexity:** High
- **Priority:** Important

---

### F-47 · Living Sync v2 (Bidirectional + Revert Handles) & Story Agent L3

- **Description:** Bidirectional diff-and-rationale sync; drift surfaced, never accepted as weather. Story agent earns L3 — pushes epic tree + ACs to Jira with diffs, rationale, and a **revert handle**; Living Sync keeps spec↔tickets coherent thereafter.
- **User Problem Solved:** Document graveyards/drift (problem 3) at full strength — two-way coherence.
- **Business Value:** The "drift surfaced, never accepted as weather" promise; L3 push is where real autonomous execution begins.
- **Dependencies:** F-31, F-44, F-25, F-19.
- **AI Concepts Used:** Bidirectional diff reconciliation; rationale generation; L3 autonomous tool-use.
- **Complexity:** High
- **Priority:** Important

---

### F-48 · Analytics Agent & Outcome Attribution v1

- **Description:** Specialist A7 + the outcome loop: instrumentation, outcome attribution, metric truth. Predicted vs. realized impact scorecards per team and per assumption class (e.g. "+6.2% vs +8% predicted"). Closes the measurement window → OutcomeReport → corpus → light necropsy → anti-pattern memory → scorecard updates everywhere it's quoted. Engineering floor: pre-registered prediction + defined measurement window + holdout/diff-in-diff where feasible, honest uncertainty bands, reported misses.
- **User Problem Solved:** Outcome unaccountability (problem 7) — predicted vs. realized never compared; judgment never compounds.
- **Business Value:** Feeds the decision-outcome corpus (the compounding moat); prerequisite for the SLA'd outcome tier; "narrative honesty" metric is a CFO trust-builder.
- **Dependencies:** F-15, F-28, F-13, F-17, F-16 (anti-pattern memory).
- **AI Concepts Used:** Outcome attribution; causal-adjacent inference (holdout/DiD); calibration; uncertainty quantification.
- **Complexity:** High
- **Priority:** Important

---

### F-49 · Release Agent & Launch Control

- **Description:** Specialist A8: readiness, rollout, comms, loop closure. Launch Control ships against readiness gates, notifies promised accounts, and **arms outcome measurement**.
- **User Problem Solved:** Launches that don't close the loop (problem 7) — outcome measurement never armed; promised accounts never told.
- **Business Value:** Connects shipping to the outcome loop and the commitment ledger; completes the canonical end-to-end trace.
- **Dependencies:** F-48 (arms measurement), F-50 (notifies promised accounts), F-17.
- **AI Concepts Used:** Readiness assessment; comms generation; loop-closure orchestration.
- **Complexity:** Medium
- **Priority:** Important

---

### F-50 · Commitment Ledger & Promise Radar

- **Description:** Extract customer-facing promises (a call → `Commitment`s), track them to roadmap, and alert on delivery risk to named accounts.
- **User Problem Solved:** Feedback black hole (problem 6) — commitments tracked in people's heads; Carlos logs an ask once and sees status forever.
- **Business Value:** Closes the loop for sales/CS (Carlos); ties promises to revenue-bearing accounts.
- **Dependencies:** F-10 (Commitment extraction), F-39 (roadmap link), F-43 (risk alerting).
- **AI Concepts Used:** Commitment extraction; promise-to-roadmap matching; delivery-risk prediction.
- **Complexity:** Medium
- **Priority:** Important

---

### F-51 · Product Strategist Agent, Counterfactual Simulator v1 & Synthetic Pre-Mortems

- **Description:** Specialist A1: strategy coherence, drift, synthetic pre-mortems, and the Counterfactual Simulator — portfolio what-ifs over the org's own outcome history + priors, **always rendered as hypothesis (Violet)**. Synthetic stakeholders power the Pre-Mortem in the Decision Sheet.
- **User Problem Solved:** Prioritization theater (problem 4) — replaces invented inputs with simulation over real outcome history.
- **Business Value:** Ends prioritization theater; the Violet "anything not yet real is violet" rule keeps simulation honestly separated from fact.
- **Dependencies:** F-48 (outcome history), F-15, F-16, F-30 (Pre-Mortem surface).
- **AI Concepts Used:** Counterfactual simulation; synthetic-stakeholder generation; scenario modeling over priors.
- **Complexity:** High
- **Priority:** Important

---

### F-52 · PM Craft Engine (beta)

- **Description:** Benchmarks artifacts/decisions against the org's *own outcome-validated best work* and coaches juniors in-line. Procedural memory of gold standards and anti-patterns.
- **User Problem Solved:** Inconsistent craft at scale (problem 9); Fatima learns the craft inside PMOS (generational lock-in).
- **Business Value:** The growth-vector / lock-in feature; raises the floor of PM quality across the org.
- **Dependencies:** F-48 (outcome-validated best work), F-16 (procedural memory), F-20.
- **AI Concepts Used:** Quality benchmarking; in-line coaching generation; gold-standard comparison.
- **Complexity:** Medium
- **Priority:** Important

---

### F-53 · Defense Room (Auto-Reporting against Hostile Q&A)

- **Description:** The rehearsal half of Auto-Reporting: exec/board reporting rehearsed against hostile Q&A, every claim provenance-linked.
- **User Problem Solved:** Status-report tax + Eddie's need for one trustworthy narrative that survives board scrutiny.
- **Business Value:** Turns reporting from a chore into a defensible, rehearsed exec asset.
- **Dependencies:** F-32 (Standing Brief), F-14, F-13.
- **AI Concepts Used:** Adversarial Q&A generation; claim-grounded narrative defense.
- **Complexity:** Medium
- **Priority:** Optional

---

### F-54 · Enterprise Hardening: Cells, BYOK, ISO 27001, Durable Orchestration

- **Description:** The named end-state landing in Year-2: cell-based architecture (VPC + event bus + Postgres + Qdrant + Redis + services + KMS per cell, one Terraform stamp, residency by region, dedicated cells / customer-VPC for enterprise, per-tenant Qdrant collections fail-closed); BYOK via KMS external key store; ISO 27001; Kafka/MSK transport; Temporal durable orchestration. Migration is event-replay + router-flip, not a rewrite.
- **User Problem Solved:** Enterprise residency/isolation/key-custody requirements; saga complexity and audit-replay becoming contractual.
- **Business Value:** Unlocks enterprise tier and the largest tenants; blast radius ≤4% per cell.
- **Dependencies:** F-01, F-04, F-17, F-12, F-44 (audit-replay), F-41.
- **AI Concepts Used:** None (platform).
- **Complexity:** High
- **Priority:** Important
- **Triggers (from §25):** Temporal at ≥3-mutation sagas or contractual replay (Q7); generic graph at ~1:4 polymorphic ratio (Q6); OpenSearch at <0.95 exact-match recall (Q8).

---

### F-55 · Generic Bitemporal Typed Graph (PKG) Layer

- **Description:** The full generic ~30-node/~60-edge bitemporal provenance graph + hash-chained Decision Ledger, **layered over** the relational tables (the tables are not migrated). The conceptual/queryable PKG made physical for heterogeneous enterprise topologies.
- **User Problem Solved:** Highly heterogeneous enterprise relationships that the fixed Product→Feature→Epic→Story tree can't express without routing through polymorphic edges.
- **Business Value:** Supports enterprise topologies and Year-3 portfolio mode; the conceptual model finally realized physically.
- **Dependencies:** F-03, F-11, F-21.
- **AI Concepts Used:** Graph reasoning; bitemporal provenance.
- **Complexity:** High
- **Priority:** Optional
- **Trigger:** Polymorphic-to-strict-FK row ratio crosses ~1:4 on the three largest tenants (Q6/TD-6); expected Year-2.

---

# 5. Future

*Year-3+ — "The Operating System." PMOS becomes infrastructure others build on.*

---

### F-56 · PMOS Platform & Public API

- **Description:** Third-party read/write with governance; agent-to-agent interoperability with external code agents. Built on the same `/api/v1` contract and BFF discipline established in Foundation.
- **User Problem Solved:** AI tool sprawl (problem 10) at the ecosystem level — PMOS becomes the decision layer other tools integrate with.
- **Business Value:** "Become infrastructure" — the final step of the guiding sequence; platform network effects.
- **Dependencies:** F-05, F-18 (governance for third-party writes), F-19.
- **AI Concepts Used:** Agent-to-agent protocols; governed external tool-use.
- **Complexity:** High
- **Priority:** Optional

---

### F-57 · L4 Autonomy GA (Earned Task Classes)

- **Description:** Fully autonomous (L4) operation for task classes that have earned it through measured accuracy. The top rung of the Trust Ladder.
- **User Problem Solved:** Maximal leverage — fewer humans supervising more automated execution.
- **Business Value:** The ultimate consumption-revenue driver; counter-positions against shrinking PM teams.
- **Dependencies:** F-44, F-48 (sustained accuracy evidence), F-20.
- **AI Concepts Used:** Fully autonomous agents; accuracy-gated promotion to L4.
- **Complexity:** High
- **Priority:** Optional

---

### F-58 · Multi-Product Portfolio Mode

- **Description:** Portfolios as a peer layer above Streams for multi-product enterprises; CQRS projections for portfolio rollups.
- **User Problem Solved:** Multi-product enterprises need cross-product coherence above the Stream altitude.
- **Business Value:** Expansion into the largest enterprises; higher ACV.
- **Dependencies:** F-36, F-55, F-35.
- **AI Concepts Used:** Cross-product aggregation/reasoning.
- **Complexity:** Medium
- **Priority:** Optional

---

### F-59 · M&A Diligence Mode

- **Description:** A mode that turns the decision corpus and audit fabric into an M&A diligence surface.
- **User Problem Solved:** Diligence teams need a trustworthy, auditable view of a product org's decisions and outcomes.
- **Business Value:** A premium enterprise/finance use case leveraging the insurance-grade audit fabric.
- **Dependencies:** F-21, F-28, F-48.
- **AI Concepts Used:** Decision/outcome corpus analysis.
- **Complexity:** Medium
- **Priority:** Optional

---

### F-60 · Anonymized Cross-Tenant Priors

- **Description:** Opt-in differential-privacy aggregation of decision-class *outcome statistics* — never content. Improves priors for all participating tenants while keeping customer decision data "radioactive-sacred."
- **User Problem Solved:** Cold-start weakness of per-tenant learning for new or small tenants.
- **Business Value:** A defensible, privacy-preserving network effect on top of the per-tenant moat.
- **Dependencies:** F-48, F-60 requires mature outcome corpus and DP infrastructure.
- **AI Concepts Used:** Differential privacy; federated/aggregate statistics; prior construction.
- **Complexity:** High
- **Priority:** Optional

---

### F-61 · The AI CPO Console

- **Description:** The capstone surface — the AI Chief Product Officer's console where the autonomous management work is supervised and the human CPO does leadership.
- **User Problem Solved:** Alex's core fear — "I'm accountable for decisions I can't see" — fully answered at the org altitude.
- **Business Value:** The realization of the product vision's headline promise; the executive supervision layer over full autonomy.
- **Dependencies:** F-57, F-43, F-48, F-35 (Org altitude).
- **AI Concepts Used:** Autonomous management orchestration; executive-grade synthesis.
- **Complexity:** High
- **Priority:** Optional

---

# Recommended Build Order

The spec hands us the critical path almost verbatim in §20:

> cell stamp + Clerk/tenancy + connector SDK (6 connectors) + ingestion→ER + PKG/Postgres + Qdrant/lexical fan-out + retrieval v1 + Diagnostic → agent runtime (Conductor/Research/PRD/Story L1/L2) + policy-engine v1 (L0–L2) + tool-svc + one-way sync → ledger ceremonies + brief/notify + search-line + metering → **eval harness gating releases (built second, not last).**

The ordering below operationalizes that into waves. The governing rule from the spec: **every Year-1 choice must be forward-compatible with the named end-state; the seams (outbox transport, orchestration, tenancy) are explicit, so later swaps are event-replay + router-flip, not rewrites.**

## Must be built first (no dependencies / unblock everything)

These have no upstream feature dependencies and gate large swaths of the backlog. Build them in this internal order:

1. **F-01 Multi-Tenancy & RLS** and **F-03 Core Schema** — bedrock; literally every content row and every query depends on them.
2. **F-02 Identity (Clerk)** — binds to tenant context; gates all human authority.
3. **F-04 Outbox & Event Backbone** — every projection and async pipeline rides it.
4. **F-06 AI Schema Spine** and **F-07 Model Gateway** — gate every AI feature and all metering.
5. **F-05 BFF & API Contract** — gates every client surface; ship the dev fixture early so frontend and backend parallelize.
6. **F-21 Audit Fabric** — gates the ledger, autonomy log, and billing meters.

> **Critical insight from the spec:** the **Eval Harness (F-20) is "built second, not last."** Stand it up immediately after the agent runtime exists (not after the agents are "done"), because edit-distance/groundedness gating is what protects the P4 quality bar — the single biggest adoption risk in the whole program.

## Features blocked by dependencies (must wait for upstream waves)

These cannot start until their named upstream features are usable. The chain is long and mostly linear through the ingestion → retrieval → generation pipeline:

- **The ingestion chain is strictly sequential:** F-08 Connectors → F-09 Screening → F-10 Extraction → F-11 Entity Resolution → F-12 Index Fan-Out → F-13 Retrieval v1. Nothing in this chain can be parallelized past its predecessor, because each consumes the previous stage's typed output. This chain is the longest pole in Year-1.
- **F-14 Claim[] / F-15 Metric Store** gate all *trustworthy* generation — F-13 produces claims, F-15 supplies every number. Both must precede any artifact or Ask feature.
- **All generative agents (F-24 PRD, F-25 Story, F-27 Research, F-29 Ask)** are blocked by F-13 + F-14 + F-17 + F-18 + F-20.
- **F-28 Ledger + F-30 Commit Ceremony** are blocked by F-21 (hash-chain) and are mutually coupled (the ceremony fills the ledger).
- **The entire V2 cross-silo layer (F-43 Sentinel, F-48 Outcome Attribution, F-50 Commitment Ledger, F-51 Simulator)** is blocked by the MVP loop existing first — you cannot detect contradictions, attribute outcomes, or simulate over history that hasn't been recorded yet.
- **F-44 Trust Ladder GA** is blocked by F-20 (edit-distance gates promotion) — autonomy promotion is *meaningless* without the measurement instrument, so F-20 must be mature before L3 ships.
- **F-54 Enterprise Hardening** and **F-55 Generic Graph** are deliberately deferred to their measurable §25 triggers (Temporal at ≥3-mutation sagas / contractual replay; generic graph at ~1:4 polymorphic ratio; OpenSearch at <0.95 recall). Building them earlier is explicitly called premature.

## Features that can be built independently (parallelizable)

Once Foundation Wave 1 exists, several tracks run in parallel:

- **F-08 Connector SDK** development can begin in parallel with the schema/event work — it only needs F-03 and F-04 stubs and is itself a long pole (six connectors).
- **F-38 Meridian Design System** and **F-35 Canvas** can be built in parallel with the backend pipeline — they need only the API contract (F-05) and, for provenance rendering, the Claim[] shape (F-14). The design system is the largest frontend track and should start in Wave 1.
- **F-34 Metering** rides on F-06 token accounting and F-21 meters; it can be built alongside the agents rather than after them.
- **F-41 Compliance / F-42 Resilience** are cross-cutting and can proceed in parallel once F-01/F-02/F-21 exist; they should not be left to the end since they gate deals.
- **The Line (F-33)** front-end shell can be scaffolded against the dev fixture before retrieval is live, then wired to F-13/F-29 when ready.

## Dependency Graph

Arrows read **A → B = "A must exist before B."** Grouped by wave.

```
WAVE 0 — FOUNDATION SUBSTRATE (no upstream deps)
─────────────────────────────────────────────────
  F-01 RLS/Tenancy ─┬─→ F-03 Core Schema ─┬─→ F-04 Outbox/Events
                    │                     ├─→ F-06 AI Schema Spine ──→ F-07 Model Gateway
                    │                     ├─→ F-21 Audit Fabric
                    └─→ F-02 Identity      └─→ F-05 BFF/API Contract
                                                   │
                            (dev fixture lets frontend start here)

WAVE 1 — INGESTION → RETRIEVAL PIPELINE (the long pole; strictly sequential)
─────────────────────────────────────────────────
  F-04,F-03 ─→ F-08 Connector SDK ─→ F-09 Screening ─→ F-10 Extraction
                                                          │
                                          F-11 Entity Resolution ←─┘
                                                          │
   F-07 ─────────────────────────────→ F-12 Index Fan-Out (Qdrant)
                                                          │
   F-15 Metric Store ──┐                                  ▼
                       └──────────────→ F-13 Hybrid GraphRAG Retrieval v1
                                                          │
                                          F-14 Claim[] / Provenance ←┘

WAVE 2 — AGENT RUNTIME & AUTONOMY  (needs F-07,F-06,F-04,F-02)
─────────────────────────────────────────────────
  F-17 Agent Runtime ─┬─→ F-18 Policy Engine/Tokens (L0–L2) ─→ F-19 Tool Service
                      └─→ F-20 EVAL HARNESS  ◀── "built second, not last"
  F-16 Memory Plane (needs F-03,F-12,F-06) ──→ feeds all agents

WAVE 3 — MVP LOOP  (needs Waves 1–2)
─────────────────────────────────────────────────
  F-13,F-14,F-17,F-18,F-16,F-20 ─→ F-24 PRD Agent ─→ F-25 Story Agent
  F-13 ─→ F-27 Research Agent ─→ F-22 Feedback Intelligence ─→ F-23 Diagnostic
  F-21 ─→ F-28 Decision Ledger ⇄ F-30 Commit Ceremony
  F-28 ─→ F-29 Ask-the-Brain   ;  F-28,F-14 ─→ F-32 Brief/Notify
  F-17,F-26 Conductor (orchestrates the above)
  F-24/25 ─→ F-31 Living Sync v1 (one-way)
  F-13,F-29 ─→ F-33 The Line ;  F-06,F-21 ─→ F-34 Metering

WAVE 4 — V1 SURFACES (parallel front-end track + early specialists)
─────────────────────────────────────────────────
  F-05,F-14 ─→ F-35 Canvas ─→ F-36 Streams/Lenses ; F-38 Design System (parallel from Wave 0)
  F-05 ─→ F-37 Tide/Meridian Bar
  F-17 ─→ F-39 Roadmap Agent ; F-13,F-15 ─→ F-40 Prioritization Agent
  F-41 Compliance ; F-42 Resilience  (cross-cutting, parallel)

WAVE 5 — V2 "AI PRODUCT TEAM MEMBER"
─────────────────────────────────────────────────
  F-16,F-13,F-37 ─→ F-43 Sentinel/Contradiction Engine
  F-18,F-20,F-21 ─→ F-44 Trust Ladder GA (L0–L3) ─→ F-45 Overnight PM
  F-28,F-10 ─────→ F-46 Archivist
  F-31,F-44 ─────→ F-47 Living Sync v2 + Story L3
  F-15,F-28 ─────→ F-48 Analytics/Outcome Attribution ─┬─→ F-49 Release Agent/Launch Control
                                                       ├─→ F-51 Strategist/Counterfactual Sim
                                                       └─→ F-52 PM Craft Engine
  F-10,F-39,F-43 ─→ F-50 Commitment Ledger/Promise Radar
  F-32 ──────────→ F-53 Defense Room
  (triggered) ───→ F-54 Cells/BYOK/Temporal/ISO ; F-55 Generic Graph

WAVE 6 — FUTURE "THE OPERATING SYSTEM"
─────────────────────────────────────────────────
  F-05,F-18,F-19 ─→ F-56 Platform/Public API
  F-44,F-48 ──────→ F-57 L4 GA
  F-36,F-55 ──────→ F-58 Portfolio Mode
  F-21,F-28,F-48 ─→ F-59 M&A Diligence
  F-48 ───────────→ F-60 Cross-Tenant Priors (DP)
  F-57,F-43,F-48 ─→ F-61 AI CPO Console
```

## The three longest poles (where schedule risk concentrates)

1. **The ingestion → retrieval pipeline (F-08 → F-13).** Strictly sequential, six stages, each high-complexity. This is the spine of the wedge and cannot be shortcut. Start the Connector SDK in parallel with Wave 0 to claw back time.
2. **Generated-artifact quality (F-24/F-25 gated by F-20).** Not a code-length problem but a *quality-convergence* problem with a defined kill/pivot trigger (>30% edit-distance after two quarters). The eval harness must be mature early so tuning has runway.
3. **Trust/autonomy progression (F-18 → F-20 → F-44 → F-57).** Each rung is earned by *measured* accuracy over time, so the calendar — not the code — is the constraint. The token machinery (F-18) ships in Year-1 specifically so the measurement clock starts as early as possible.

---

*This inventory is built against Year-1 implementation as authoritative per the spec's rule of precedence. Where a feature names an end-state it must not foreclose, that is recorded on the feature so the forward-compatible seam is preserved. Engineering teams can begin implementation planning directly from the Wave structure and dependency graph above.*
