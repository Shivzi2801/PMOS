# PMOS — MASTER SPECIFICATION
### The Product Management Operating System · The Single Source of Truth

**Status:** Constitution v1.0 (consolidates Documents 01–08) · **Audience:** engineers, designers, AI architects, and AI coding assistants · **Rule of precedence:** where any prior document conflicts with this one, *this document wins*. Conflicts found in the source set are resolved in §22 (Technical Decisions); the resolutions there are binding. §25 (Open Questions) lists what remains genuinely unsettled, each with a recommended resolution and a default to operate on now — technical items are safe to design against; items tagged *needs validation* await design-partner data and must not be treated as closed.

---

## 0. How to read this document

This is a decisions document, not an explainer. Each section states what is true and what is built, not why at length (the "why" lives in 01–08). Two recurring distinctions govern everything:

- **Vision invariants vs. phased implementation.** Several source documents describe an aspirational end-state (cells, Kafka, Temporal, a generic bitemporal graph) while the executable backend (Doc 07) and data model (Doc 05) describe what Year-1 actually ships (shared-schema RLS, Redis Streams, a NestJS orchestrator, a relational hierarchy). Both are kept. The invariant is named; the phasing is explicit. **Year-1 implementation is authoritative for what gets built now; the end-state is the named target it must not foreclose.**
- **Claims, not strings.** Every AI-generated prose field anywhere in the system is a `Claim[]` (`{text, citations[], kind, confidence}`), at the wire level. This is not a convention; it is the protocol.

---

## 1. Product Vision

> **Every product organization runs on PMOS the way finance runs on the general ledger: a single, continuously updated, machine-reasoned source of truth for what to build, why, and whether it worked — with an AI Chief Product Officer that executes the operational work of product management autonomously and gets measurably smarter with every decision the company makes.**

PMOS is not an AI writing assistant for PMs. Document generation is a commodity. PMOS is the **decision infrastructure layer of the product organization** — the system of record for *decisions*, holding the living state of "what we know, what we decided, why, and what happened," and doing the work that flows out of that state autonomously.

**Category:** Product Management Operating System (the "Product Decision Layer"). Work-management tools (Jira, Linear) are the *system of execution*; feedback tools (Productboard, Dovetail) are *systems of record*; PMOS is the **system of decision** above both.

**Positioning:** *"PMOS does the product management; your people do the product leadership."*

---

## 2. Product Principles

1. **Decisions are the product; documents are exhaust.** The first-class object is the Decision, not the artifact.
2. **The graph is the system; retrieval is a view of it.** Conceptually a Product Knowledge Graph (PKG); physically a relational system of record with derived indexes (§13–14).
3. **Every generated sentence is a contract.** No uncited prose. Provenance is a material, resolvable in <400ms.
4. **Autonomy is enforced in the runtime, never in the prompt.** A capability the agent's session lacks cannot be exercised by any instruction.
5. **The model layer is rented; the learning loops are owned.** Frontier models are swappable behind a gateway. The decision-outcome corpus, calibration, and anti-pattern memory compound per tenant.
6. **Ingested content is hostile until proven otherwise.** All source content is data, never instructions.
7. **Elevate the human, visibly.** Attribution, calibration, and outcomes are surfaced in the human's favor by default. Adoption depends on it.
8. **Honest degradation, never silent staleness.** Every read surface carries freshness; on SLO breach the UI renders the staleness.
9. **Speed everywhere; ceremony only where it matters** (commit, grant authority, ship).
10. **Append-only for anything auditable.** Decision ledger, autonomy log, billing meters: hash-chained, `UPDATE`/`DELETE` revoked at the role level.

---

## 3. Core User Personas

| ID | Persona | Role | Relationship to PMOS |
|---|---|---|---|
| P1 | **Accountable Alex** | CPO / VP Product | Economic buyer; wants visibility, coherence, leverage |
| P2 | **Stretched Sam** | Senior / Group PM | Power user; wants to be a strategist, not a secretary |
| P3 | **Operations Olivia** | Product Operations Lead | Champion & admin; encodes standards once, enforced everywhere |
| P4 | **Skeptical Priya** | Engineering Lead | Adjacent user & veto-holder; quality bar for generated stories/ACs is *engineer-approved* |
| P5 | **Exposure Eddie** | CEO / CFO / Board | Report consumer; wants one trustworthy narrative |
| P6 | **Frontline Fatima** | Junior PM / APM | Growth vector; PMOS is where she learns the craft (generational lock-in) |
| P7 | **Closing Carlos** | Sales / CS Leader | Signal source & loop-closer; logs an ask once, sees status forever |

**Buyer ≠ User ≠ Beneficiary.** Sell to the product leader's fear ("I'm accountable for decisions I can't see"). Viewers (the whole company) are free and unlimited — ubiquity is the expansion engine.

---

## 4. User Problems (the problems PMOS exists to kill)

1. **Synthesis ceiling** — feedback grew ~10× while PM headcount is flat; orgs can't read what customers tell them.
2. **Status-report tax** — 30–50% of PM time on internal artifacts that are stale on arrival.
3. **Document graveyards / drift** — PRDs diverge from tickets within days; nobody maintains the spec.
4. **Prioritization theater** — RICE/WSJF inputs are invented; roadmap items untraceable to evidence.
5. **Institutional amnesia** — decision rationale stored nowhere; orgs relitigate and repeat failures.
6. **Feedback black hole** — customer/field feedback vanishes; commitments tracked in people's heads.
7. **Outcome unaccountability** — predicted vs. realized impact never compared; judgment never compounds.
8. **Risk discovered in post-mortems** — cross-silo contradictions invisible until they explode.
9. **Inconsistent craft at scale** — PM quality variance; the CPO's brand is hostage to the weakest PRD.
10. **AI tool sprawl, zero compounding** — 5–10 disconnected tools, each starting from zero context.

---

## 5. Product Scope

**In scope:** the closed loop **signal → insight → decision → artifact → execution → outcome → learning**, plus the governance (Trust Ladder), memory (graph + ledger + outcome corpus), and reporting that make the loop trustworthy and autonomous.

**Out of scope (delegated, never replicated):** ticket execution (Jira/Linear/ADO remain the engineer's tool — PMOS syncs, does not replace), analytics instrumentation engines, meeting recording, and the frontier models themselves.

---

## 6. Core Features

The feature set is the loop made operable. Each feature is structurally unavailable to document-generators and task databases because each requires the graph + ledger + outcome loop to exist.

1. **Feedback Intelligence (the wedge)** — every signal clustered, quantified, tied to accounts/revenue, threaded to the decisions it should inform. *Kills problems 1 & 6.*
2. **Decision Ledger** — every decision a first-class versioned object: options, evidence, assumptions, predicted impact, owner, dissent, review date. "git for product decisions." *Kills problem 5.*
3. **Evidence-Native Artifacts** — PRD/epic/story/AC generation where every sentence traces to sources. *Answers P4's "AI slop" veto.*
4. **Living Sync** — bidirectional, diff-and-rationale sync between the spec layer and Jira/Linear/ADO; drift surfaced, never accepted as weather. *Kills problem 3.*
5. **Outcome Attribution** — predicted vs. realized impact scorecards per team and assumption class. *Kills problem 7.*
6. **Auto-Reporting (Standing Brief + Defense Room)** — continuously-current exec/board reporting, every claim provenance-linked; rehearsal against hostile Q&A. *Kills problem 2.*
7. **Commitment Ledger & Promise Radar** — extract customer-facing promises, track to roadmap, alert on delivery risk to named accounts.
8. **Counterfactual Simulator** — portfolio what-ifs over the org's own outcome history + priors; always rendered as hypothesis (Violet). *Ends problem 4 theater.*
9. **Contradiction Engine / Strategy Drift Detector** — cross-silo conflict detection; stated strategy vs. actual resource allocation. *Kills problem 8.*
10. **Org-Wide Product Brain** — anyone asks "why don't we support SSO on Starter?" and gets decision + evidence + owner + review date.
11. **PM Craft Engine** — benchmarks artifacts/decisions against the org's own outcome-validated best work; coaches juniors in-line. *Kills problem 9.*
12. **The Overnight PM** — a standing autonomous shift that triages, refreshes, drafts, and queues, so every day starts at "review and decide."

---

## 7. AI Capabilities

PMOS's AI is organized as **four planes** (Knowledge → Memory → Agent → Learning).

- **Knowledge:** connectors → normalization → PII & injection screening → enrichment (signal extraction is the crown jewel: a ticket becomes typed `FeedbackAtom`s, a call becomes `Commitment`s, a Slack thread becomes `DecisionCandidate`s) → entity resolution → graph upsert → index fan-out.
- **Retrieval (Hybrid GraphRAG):** parallel vector + lexical + typed graph traversal + governed metric-store tool calls + ledger lookups → fusion → rerank → ACL-trimmed select → **claim-grounded generation** → groundedness verification. **Numbers are tools, not text** — any quantitative claim must originate from a governed metric-store call.
- **Memory:** four cognitive types — working (task workspace), episodic (Decision Ledger + run logs, permanent), semantic (the graph/indexes), procedural (templates, scoring models, anti-patterns, human-governed) — across three scopes: organizational, product, user.
- **Learning:** the decision-outcome corpus, edit-distance/acceptance signals, per-tenant reranker training, calibration records, anti-pattern memory. A model upgrade improves PMOS overnight with zero migration; a competitor on the same model is still years behind on a tenured tenant.

**Hard AI invariants:**
- Every generated prose field is `Claim[]` with sentence-level citations; uncitable claims are marked `inference` and rendered differently.
- Confirmation bias is fought structurally: drafting/prioritization agents run a mandatory **contrarian probe** ("evidence against").
- **Models:** Anthropic Claude (`claude-sonnet-4-6` default per `ai_agents.model`) for agents; OpenAI `text-embedding-3-large` @ **3072 dims** for vectors. (This supersedes the AI doc's Voyage-1024-d/Matryoshka proposal — see §22 TD-5.)
- Per-task tiered routing (frontier / mid / small / embedding) behind a single Model Gateway with ZDR contracts; no cross-tenant training by default.

---

## 8. Agent Ecosystem

**Eleven agents:** three system agents + eight specialists. An agent is a versioned software unit (role charter, tool manifest, memory lens, autonomy matrix held by the policy engine, model binding, eval suite, KPIs), **stateless between tasks** — all state lives in the Task Workspace and memory plane, so every run is replayable and auditable.

| | Agent | Mission |
|---|---|---|
| S1 | **Conductor** | AI Chief of Staff: intake, planning, delegation, assembly, submission to humans |
| S2 | **Sentinel** | Standing watch: contradictions, drift, risk, promise radar (the engine behind the Tide) |
| S3 | **Archivist** | Memory steward: decision capture from the wild, consolidation, provenance repair |
| A1 | **Product Strategist** | Strategy coherence, drift, synthetic pre-mortems, the Counterfactual Simulator |
| A2 | **Research** | Evidence: feedback synthesis, interviews, market context |
| A3 | **Roadmap** | The living plan: sequencing, capacity, dependencies, scenarios |
| A4 | **Prioritization** | Defensible ranking, trade-offs, counterfactuals |
| A5 | **PRD** | Decision → build-ready, evidence-native spec |
| A6 | **Story Writing** | Spec → epics/stories/ACs in execution tools, engineer-grade |
| A7 | **Analytics** | Instrumentation, outcome attribution, metric truth |
| A8 | **Release** | Readiness, rollout, comms, loop closure |

**Trust Ladder (the autonomy contract):** L0 observe → L1 draft → L2 act-with-approval → L3 act-and-notify → L4 autonomous. **Authority is a consumable, audited capability token bound to (run, task-class, approval event, 5-min TTL), issued by the policy engine, verified cryptographically at the tool service.** Promotion is earned by measured accuracy and granted by humans (a signed ledger decision); demotion on bar breach is automatic and logged. Year-1 implementation: L2 artifacts require human approval endpoints; agent writes carry `ai_generated=true` + `source_run_id` (§22 TD-3).

---

## 9. User Workflows

The canonical loop, end to end (one worked trace):

1. **Signal lands** (e.g., Zendesk ticket) → screened → extracted into FeedbackAtoms + RiskSignal → entity-resolved to an Account → upserted with provenance → indexed.
2. **Sentinel** detects a cross-silo contradiction (rising churn on an account whose audit-log promise is slipping) → a Now-tier Tide item.
3. **The Brief** (06:00) leads with the finding; the PM opens the **Decision Sheet**, reads The Question and The Call, runs a **Pre-Mortem** (synthetic stakeholders), adds a guard ("gate at 5% until assumption A3 verifies"), and **commits** (typed initial, the ceremony writes the hash-chained ledger entry).
4. An **L2 PRD agent** drafts evidence-native, queues it; the PM edits non-goals (edit-distance logged) and approves.
5. An **L3 Story agent** (earned) pushes the epic tree + ACs to Jira with diffs+rationale and a revert handle; Living Sync keeps spec↔tickets coherent thereafter.
6. **Launch Control** ships against readiness gates, notifies promised accounts, and **arms outcome measurement**.
7. Weeks later **Analytics** closes the window (+6.2% vs +8% predicted) → OutcomeReport → corpus → a light necropsy ("assumption A2 over-indexed") → anti-pattern memory → the scorecard updates everywhere it's quoted.

*Total human time: judgment only. Everything else is recorded, cited, reversible.*

---

## 10. Information Architecture

**Seven first-class objects** form a left-to-right causal flow that *is* the IA: **Signal ◦ → Insight ◎ → Decision ◆ → Artifact ▤ → (Execution) → Outcome ▲▼**, with **Commitment ⬡** and **Agent Run ⟳** cross-cutting.

- **The Meridian:** one horizontal spatial axis. Left = evidence/past; right = plans/future; **outcomes flow backward** (right→left) and attach to the decisions that predicted them.
- **Three altitudes:** Org (30k ft, the CPO's resting view) · Stream (3k ft, the PM's working altitude) · Object (ground, a single Sheet).
- **Containers:** Workspace = one company = one graph; **Stream** = the only human-curated container (a durable area of responsibility); **Lens** = a saved, shareable canvas configuration; **Brief** = a generated ephemeral narrative (re-rendered from the ledger, never stored stale). Portfolios are a peer layer above Streams for multi-product enterprises (Year-3).
- **Deliberately does not exist:** folders, page trees, a "docs" section, a global projects list, the dashboard-of-widgets. If a user wants to "organize," retrieval has failed — they ask the Line.

**Persistence-layer hierarchy (the system-of-record realization of the IA):** `Organization → Workspace → {Products → Features → Epics → User Stories → Requirements}`, plus Roadmaps/Releases, Feedback/Interviews/Insights, and Documents (with immutable versions + chunks). The generic typed-graph framing of the PKG is the *conceptual/queryable* layer projected over these tables plus provenance/link tables (§22 TD-6).

---

## 11. Design Principles (The Five Laws)

1. **The system speaks first.** Every surface opens with a finding (what changed, what it means, what's recommended) — never an empty input or a grid of widgets.
2. **Evidence is a material, not a feature.** Provenance is everywhere, structural, openable in <400ms. There is no "citations panel"; there are no uncited sentences.
3. **Autonomy is visible, graduated, and reversible.** The user can always answer: what is the AI doing, under what authority, how do I take the wheel.
4. **One surface, many lenses; never many apps in a trenchcoat.** One canvas, one command interface (the Line). "Screens" are lenses over the same graph.
5. **Elevate the human, visibly.** The AI's work is always a draft *submitted to* the human; attribution and outcomes favor the human by default.

**The one-sentence review test:** *Does this screen make a human more decisive, more evidenced, and more credited than they were yesterday?*

---

## 12. Design System Summary — "Meridian"

- **Aesthetic:** "Calm Authority" — the cockpit of a Gulfstream at cruise; the reading room of a great library.
- **Two atmospheres** switched by *content mode*, not OS theme: **Daylight** (`#FAFAF7` paper, `#16161A` ink) for reading; **Midnight** (`#0C0D10`, `#EDEDEF`) for monitoring agents. 600ms crossfade.
- **Exactly five semantic hues**, used only when they carry meaning: **Verdant** `#1FA97A` (verified/kept/earned) · **Signal Blue** `#3B6FF6` (live AI/citations) · **Amber** `#E8A33D` (pending/expiring/drift) · **Vermilion** `#E5483F` (contradiction/broken/risk) · **Violet** `#8B5CF6` (simulation/hypothetical — *anything not yet real is violet*). The chrome is monochrome.
- **Typography (role-correct):** sans for chrome, **serif for artifacts/evidence** (documents read like the record), mono for IDs/hashes/metric deltas. 15px base, 1.2 modular scale, nothing below 12px.
- **The signature element — the Provenance Underline:** thickness encodes evidence weight (1px single source · 2px corroborated · dotted inference · violet simulated), with redundant non-color encoding (thickness + glyph) for accessibility. CSS classes: `.prov-single / .prov-corroborated / .prov-inference / .prov-simulated / .prov-degraded`.
- **Motion:** physics, not animation. One spring curve (`stiffness 320 / damping 32`); things arrive from and return to their source; AI "thinking" is a luminous pulse, never a spinner; motion budget 350ms/interaction; `prefers-reduced-motion` replaces travel with crossfade.
- **Navigation:** the **Line** (`⌘K`, three blended modes — Go/Ask/Do); the **Meridian Bar** (bottom strip with waypoints `⌘1–5`, time scrubber, altitude); the **Tide** (calm ranked notifications, interrupting only for Vermilion). Keyboard doctrine: 100% pointer-free.
- **Accessibility/i18n:** WCAG 2.2 AA in both atmospheres; full keyboard = the screen-reader spine; per-reader localization from one graph.

---

## 13. Technical Architecture Summary

**Stack (as built, Doc 07 authoritative):** Next.js (App Router, RSC, Vercel) · NestJS 10 (all backend services) · PostgreSQL 16 · Qdrant (vectors) · Redis (ioredis, cache + Streams) · BullMQ (queues) · Clerk (identity) · Supabase Storage (blobs) · Anthropic Claude + OpenAI embeddings behind a Model Gateway · Docker · AWS.

**Three traffic classes:**
1. **Interactive reads** (Brief, Peek, canvas, Ask-first-token) — served from precomputed projections / Redis / Postgres; no LLM in the hot path except Ask token streaming.
2. **Interactive writes** (commit, approve, edit) — BFF → domain service → Postgres tx (state + outbox) → event fan-out; ceremonial writes add hash-chain append + signature verification.
3. **Autonomous work** (ingestion, Overnight shift, agent tasks) — event/queue-driven, never user-blocking, batch lanes on the Model Gateway.

**Event backbone:** transactional **outbox** is the invariant (no event without the state change in the same transaction). Transport is **Redis Streams** Year-1 (relay worker), with a Kafka/MSK-swappable seam for cell scale (§22 TD-2).

**Agent orchestration:** logical contract is stateless, checkpointed, replayable, capability-gated runs. Year-1 implementation is a NestJS orchestrator + BullMQ; **Temporal** is the named durable-engine target at scale (§22 TD-4).

**Performance budgets (enforced in CI; a regression is a release blocker):** Line Go <50ms · Peek <100ms · Sheet (cached) <150ms · Ask first token <700ms · Provenance Lens <400ms · cold load to interactive Brief <1.5s · canvas pan/zoom ≥60fps.

---

## 14. Database Summary

- **System of record:** PostgreSQL 16. **Vectors are derived** and always rebuildable from Postgres — we never store anything in Qdrant that cannot be regenerated.
- **Keys:** UUIDv7 everywhere (time-ordered; doubles as the Qdrant point ID — no mapping table).
- **Conventions:** `timestamptz` always; `updated_at` by trigger; soft delete (`deleted_at`, 30-day trash, then purge = the GDPR erasure path); `organization_id` **and** `workspace_id` on every content row (single-column RLS + ready-made shard key + direct Qdrant payload filter).
- **Product hierarchy is explicit, first-class tables** (`products → features → epics → user_stories → requirements`), not a generic work-item table — PMs and agents both reason in these terms. Polymorphism is used *only* for genuinely "to-anything" edges (comments, tags, links, conversations via `(context_type, context_id)`).
- **The Postgres↔Qdrant contract is `document_chunks`:** the chunk's Postgres `id` *is* the Qdrant point ID; `content_hash` prevents redundant re-embedding; a nightly reconciler heals drift. pgvector was considered and rejected (heavily-filtered retrieval + native hybrid + OLTP contention).
- **AI is first-class in the schema:** `ai_agents`, `conversations`, `messages` (with `tool_calls`), `agent_runs` (token-metered) + `agent_run_steps` (step trace) — the audit & cost spine.
- **Embeddings:** OpenAI `text-embedding-3-large` @ 3072 dims; Qdrant collections are dimension-fixed (blue/green on model change).

---

## 15. API Strategy

**Three-protocol contract, one decision rule** (*mutates → REST command; composes ≥3 resources for one screen → GraphQL lens; server-push → SSE*):

- **REST** (`/api/v1/...`) — commands and simple reads; **`Idempotency-Key` mandatory on every POST** (scoped `(tenant, user, method, path, key)`, 24h response cache, body-hash mismatch → `409 idempotency_conflict`).
- **GraphQL** (`/api/graphql`) — lenses (Brief, Stream Canvas, Decision Sheet, Horizon, Arena); **persisted queries only in production**, complexity budget (cost ≤ 1,000, depth ≤ 8), DataLoader batching, complexity counted against the rate limit.
- **SSE** (streaming `/api/v1/...`) — Ask token/claim streaming, agent-run progress, the Tide; resumable via `Last-Event-ID`, heartbeats every 15s, server-side termination at 15 min with client resume. SSE auth uses fetch-based streaming with the `Authorization` header; native `EventSource` uses a single-use 60s **stream ticket**, never the session token.

**One async-job grammar for everything slow** (exports, imports, backfills, simulations, the Diagnostic, agent runs): `202 Accepted` + job resource (`status`, `progress`) + SSE progress + `cancel` verb.

**Wire invariants:** `Claim[]` is the protocol type for all AI prose; one error format everywhere; cursor-only pagination (no `COUNT(*)` per page); two-key versioning (URI `/v1` for structure + `PMOS-Version` date header for behavioral changes — Stripe-style rolling improvements, no `/v2` proliferation). The **BFF is the single client-facing surface**; no client ever calls a domain service, the Model Gateway, or a datastore directly. The frontend ships a built-in dev fixture implementing this exact `/api/v1` contract; pointing `NEXT_PUBLIC_PMOS_API_URL` at a real BFF bypasses the fixture with an identical client contract.

---

## 16. Security Model

**Threat model, ranked:** (1) cross-tenant exposure — existential; (2) prompt injection driving unauthorized action — the AI-native attack; (3) source-credential theft; (4) insider/over-privileged access; (5) audit-record tampering; (6) supply chain.

- **Identity:** Clerk (SSO SAML/OIDC, MFA for editor+ roles, SCIM enterprise, JWKS-verified JWTs). **Passwords never touch PMOS endpoints.** (This supersedes the DB doc's `auth_provider` password option — §22 TD-7.)
- **Two principals, two decision points:** a *human's* authority is a session claim (RBAC × ABAC × source-ACL trim); an *agent's* authority is a consumable capability token naming a single action class. An L1 agent cannot call a write tool because no token exists in its run.
- **Source-ACL inheritance (load-bearing):** every chunk/node carries the source system's `read_principals`; retrieval trims candidates *pre-fusion*; PMOS never widens access; an Ask answer computed without evidence the asker can't see renders "n sources withheld by permissions" — honest, never leaking, never silently omitting the fact of omission. ACL drift reconciled ≤1h.
- **Injection defense-in-depth:** ingestion screening (`quarantine:injection_suspect`, rendered inert) → structural prompt separation (source content only inside delimited typed evidence blocks) → tool schemas reject evidence-sourced arguments for sensitive parameters → capability confinement → continuous CI red-team with a **tool-call attack-success-rate target of 0**.
- **Audit fabric (one pattern for all):** append-only Postgres tables, each row `row_hash = H(prev_hash ‖ canonical_payload)`, hourly chain-head anchors to WORM S3, full OTel trace linkage. This is the "insurance-grade audit" sold in Year-3 and the surface security reviews inspect — deliberately the same machinery.
- **Compliance roadmap:** SOC 2 Type II → ISO 27001 → BYOK. GDPR DSAR/erasure cascade ≤24h via tombstones, leaving typed "redacted" stubs so ledger auditability survives content removal.

---

## 17. Multi-Tenancy Strategy

A single phased model with one invariant: **three independent enforcement layers must all fail for a leak.**

**Year-1 (as built):** shared database, shared schema, **PostgreSQL Row-Level Security**.
- `organization_id` on every row; canonical RLS policy + `FORCE ROW LEVEL SECURITY`; per-request `SET LOCAL app.current_org_id` inside a tenant-scoped transaction (PgBouncer transaction-pooling safe). The data layer **refuses any query lacking a TenantContext**.
- Org boundary is the *hard* RLS boundary; workspace is the *soft* app-layer boundary (join-based RLS degrades plans — deliberate defense-in-depth split).
- Qdrant: **payload-partitioned** single-collection-per-granularity with `is_tenant`-indexed `organization_id`; the org filter is force-injected from context, never from request params; Qdrant is reachable only from the retrieval service.
- Background workers run `BYPASSRLS` but are code-review-required to filter `organization_id` explicitly.

**Year-2+ target (the named end-state, must not be foreclosed):** **cell-based architecture** — a cell = VPC + event bus + Postgres + Qdrant + Redis + services + KMS keys, one Terraform stamp, residency by region, **dedicated cells / customer-VPC for enterprise**, per-tenant Qdrant collections (fail-closed), BYOK via KMS external key store. Because `organization_id` is on every row and the cell stamp ships day one (it is how staging works), the migration is event-replay + router flip, not a rewrite. (§22 TD-1.)

---

## 18. Scalability Strategy

**Design envelope:** 1,000,000 users across ~2,500 tenant orgs; ~75,000 active editors; ~925,000 read-mostly viewers; 10⁷ source records / 10⁸ chunks per large tenant; ~50M ingestion events/day and ~3M agent tasks/day platform-wide; ~50,000 concurrent interactive users peak.

- **Read scaling:** viewers (≈92% of users) hit precomputed projections — marginal cost ≈ cache hits; "unlimited viewers" is architecturally cheap *on purpose*.
- **Write scaling:** outbox + event fan-out; CQRS only where UX budgets demand precomputation (Brief, portfolios, audit timelines); projections are rebuildable from the event archive in minutes.
- **Retrieval scaling:** tiered search (coarse pre-filter → rescore → cross-encoder → LLM-select); int8 quantization; per-tenant reranker heads (base embedder untouched).
- **Ingestion scaling:** three priority lanes (live ≤2 min / standard ≤15 min / bulk best-effort); backfills never delay live contradiction detection; cheap-first cascades mean ~70% of records never touch an LLM.
- **Horizontal scaling unit:** Year-1 service replicas + read-through caches; Year-2+ the cell (25–40 cells × ~60–100 tenants, dedicated cells for enterprise; blast radius ≤4% of platform; cell creation ≈2h via Terraform).
- **Resilience:** in-cell multi-AZ HA; RTO 1h / RPO ≤5 min; model-provider failover degrades Ask to a lower tier with a *visible quality badge* (honest degradation); kill switches per-tenant/agent/tool/level.

---

## 19. Success Metrics

- **North Star:** **Decision Velocity × Decision Quality** — median time from signal to shipped, outcome-verified decision, measured per customer. (Rejected vanity metric: "documents generated.")
- **Wedge ROI (provable in 30 days):** ≥5–6 hrs/PM/week recovered (feedback intelligence + auto-reporting).
- **Adoption:** >70% weekly PM engagement; >60% of onboarding users stay to watch "The Reading."
- **Quality gate (P4 veto):** generated-story edit-distance and engineer approval latency within band (kill/pivot trigger: >30% edit-distance after two quarters of tuning).
- **Trust/autonomy:** per-(agent, task-type, team) acceptance rate; ≥40% of artifact volume at L2+ by Year-2; honesty metric ≥95% correct abstention on unanswerable sets.
- **Commercial:** Year-3 blended ACV ≈ $140K; NRR ≥130%; consumption ≥35% of revenue by Year-2.
- **Engineering SLOs:** all §13 performance budgets; Brief published by local 6am ≥99.5% of days; interactive availability 99.9%; per-task COGS within budget (measured, not estimated).

---

## 20. Future Roadmap

**Guiding sequence: Earn memory → Earn trust → Earn autonomy → Become infrastructure.**

- **Year 1 — "The Product Org's Memory."** Ingestion & graph v1; Feedback Intelligence + the free **Diagnostic** (GTM); Artifact Engine (L1/L2); Living Sync v1; Decision Ledger v1 + Ask-the-Brain v1. Autonomy capped L1–L2. *Exit: 25–40 mid-market logos.* **Build order (critical path):** cell stamp + Clerk/tenancy + connector SDK (6 connectors) + ingestion→ER + PKG/Postgres + Qdrant/lexical fan-out + retrieval v1 + Diagnostic → agent runtime (Conductor/Research/PRD/Story L1/L2) + policy-engine v1 (L0–L2 token machinery ships now) + tool-svc + one-way sync → ledger ceremonies + brief/notify + search-line + metering → **eval harness gating releases** (built second, not last).
- **Year 2 — "The AI Product Team Member."** Trust Ladder GA (L0–L3, audit/rollback); the Overnight PM; Contradiction Engine; Outcome Attribution v1; Commitment Ledger & Promise Radar; enterprise hardening (SSO/SCIM, residency, ISO 27001); Counterfactual Simulator v1; Strategy Drift Detector; PM Craft Engine beta. *Cells, BYOK, and durable orchestration (Temporal) land here.*
- **Year 3 — "The Operating System."** PMOS Platform & API (third-party read/write with governance; agent-to-agent interop with code agents); L4 GA for earned task classes; multi-product portfolio mode; M&A diligence mode; opt-in **anonymized cross-tenant priors** (differential-privacy aggregation of decision-class outcome statistics — never content); the AI CPO Console.

---

## 21. Non-Goals

- **Not a faster-typing assistant.** Document generation is a commodity; it is never the moat.
- **Not a replacement for the CPO or for human judgment.** The AI CPO does the management; humans do the leadership and own P&L. No irreversible decision without human sign-off.
- **Not a replacement for the execution tool.** PMOS syncs with Jira/Linear/ADO; it does not rip and replace them.
- **No per-seat pricing for the core, no free tier for the core, no professional-services dependency >10% of revenue, no advertising/data-resale ever.** Customer decision data is "radioactive-sacred."
- **Not model-quality, UI-beauty, or price as differentiators** — model is rented, UI polish is table stakes, we sell value not seats.
- **No autonomy before trust.** Authority is earned by measured accuracy, never defaulted.

---

## 22. Technical Decisions (binding resolutions of source-document conflicts)

| # | Decision | Conflict resolved | Resolution |
|---|---|---|---|
| TD-1 | **Tenancy = shared-schema RLS now; cells later.** | SysArch/AI mandate cells & collection-per-tenant; DB/Backend ship shared-schema RLS & payload-partitioned Qdrant. | Year-1 = RLS + payload partitioning (buildable, in Doc 07). Cells/dedicated-enterprise/BYOK = named Year-2+ target. `organization_id`-on-every-row keeps migration to event-replay + router-flip. |
| TD-2 | **Events = transactional outbox over Redis Streams now; Kafka/MSK seam.** | SysArch specifies MSK/Kafka; Backend uses Redis Streams. | Outbox is the invariant; Redis Streams Year-1; Kafka/MSK at cell scale. Swap is non-breaking by design. |
| TD-3 | **Agent authority = capability tokens (logical invariant); Year-1 enforces via approval endpoints + `ai_generated` flags.** | AI/SysArch describe a full policy-engine token runtime; Backend ships approval-gated drafts. | The token model is the contract and ships its machinery in Year-1 (L0–L2). Full L3/L4 token TTL/two-person-rule lands with Trust Ladder GA (Year-2). |
| TD-4 | **Orchestration = NestJS orchestrator + BullMQ now; Temporal target.** | AI/SysArch specify Temporal; Backend uses a Nest orchestrator. | Logical run contract (stateless, checkpointed, replayable) is the invariant. Temporal is adopted when durable-replay/saga complexity warrants it (Year-2). |
| TD-5 | **Embeddings = OpenAI `text-embedding-3-large` @ 3072 dims.** | AI doc proposes Voyage-class 1024-d Matryoshka int8. | Backend/DB config is authoritative; AI-doc specifics superseded. Schema is `embedding_model`-parameterized; collections dimension-fixed (blue/green on change). |
| TD-6 | **Knowledge model = relational hierarchy is the system of record; "PKG" is the conceptual/queryable projection.** | AI doc describes a generic ~30-node/~60-edge bitemporal provenance graph + hash-chained Ledger; DB models strict Product/Feature/Epic/Story + provenance/link tables. | Relational schema (Doc 05) is built now, with provenance/link tables and agent-run audit. Full generic bitemporal graph + hash-chained Decision Ledger is a Year-2 capability layered over it. |
| TD-7 | **Identity = Clerk; passwords never touch PMOS.** | DB `auth_provider` lists `password`/`saml`; all other docs use Clerk. | Clerk canonical (SSO/SAML/OIDC/MFA/SCIM); the DB `auth_provider` enum is retained only as an identity-source label populated from Clerk, never a credential store. |
| TD-8 | **Lexical retrieval = Qdrant native hybrid (dense+sparse) now; OpenSearch optional at scale.** | AI/SysArch add an OpenSearch BM25 leg; Backend relies on Qdrant hybrid. | Qdrant hybrid Year-1; OpenSearch introduced only if exact-match/ID recall at large-tenant scale demands a dedicated lexical engine. |
| TD-9 | **Default agent model = `claude-sonnet-4-6`, per `ai_agents.model`, behind the Model Gateway.** | Tiered multi-provider routing (AI/SysArch) vs. a concrete default (Backend). | Gateway + tiered routing is the architecture; Sonnet is the configured default; routing policy is hot-swappable per task type. |

**Standing ADRs reaffirmed:** PostgreSQL as system of record (graph + ledger) with caches; capability tokens over standing service accounts; GraphQL for lenses / REST for commands / SSE for streams; Clerk for identity with authority in PMOS policy data; Supabase Storage for user-facing blobs while S3 holds archives.

---

## 23. Assumptions

1. Mid-market CPOs (Wave 1) will grant read access to modern stacks (Slack/Linear/Notion at minimum); Gong/Salesforce raise coverage but the wedge can work on tickets+docs alone.
2. Frontier model quality continues to improve and remains rentable under ZDR terms; PMOS's moat is the owned learning loops, not the model.
3. Engineering leads (P4) will advocate once generated-story quality clears an edit-distance/approval-latency bar.
4. Source systems expose adequate webhooks/CDC and ACL data for ≤2-min freshness and ≤1h ACL reconciliation.
5. Product orgs will accept metered "autonomy unit" consumption pricing alongside a platform fee (validated in design-partner contracts).
6. Per-tenant data volumes stay within the 10⁷ records / 10⁸ chunks envelope for the relevant tier; whale tenants trigger the dedicated-cell/shard escape hatch.
7. The relational product hierarchy adequately models customers' work for Year-1; the generic graph layer is needed before highly heterogeneous enterprise topologies (Year-2+).

---

## 24. Risks

| Risk | Mitigation |
|---|---|
| **Frontier labs subsume the category.** | Moat = per-customer graph + decision-outcome corpus + governance + integrations; re-test every model release. |
| **Incumbents bundle "good enough."** | Speed on objects they can't retrofit (Ledger, outcome loop, Trust Ladder); pricing they can't copy without cannibalizing seats. |
| **PM teams shrink, shrinking the buyer.** | Counter-position: fewer humans supervising more automated execution makes the decision layer *more* valuable. |
| **Enterprises won't grant autonomy.** | Trust Ladder monetizes at L1–L2; autonomy is upside, not dependency. |
| **Cross-tenant leak (existential).** | Three independent enforcement layers + nightly cross-tenant probes (any hit = sev-0); collection/cell isolation at scale. |
| **Prompt injection drives an unauthorized action.** | Capability confinement + ingestion screening + structural separation; CI attack-success-rate target 0 at the tool-call layer. |
| **Generated-artifact quality below P4's bar.** | Evidence-native generation, contrarian probe, PM Craft gold standards, edit-distance as a Trust-Ladder gate; kill/pivot trigger defined (§19). |
| **Architecture divergence (vision vs. build) causes rework.** | §22 phasing: every Year-1 choice is forward-compatible with the named end-state; seams (outbox transport, orchestration, tenancy) are explicit. |
| **Outcome-attribution credibility with a CFO.** | Inspectable join logic; honest uncertainty bands; misses reported by policy; minimum analytic rigor validated with design partners (§27). |

---

## 25. Open Questions

These remain *open* — none is closed by fiat. Each carries a **recommended resolution** (the position to build/operate on now), a **default** (what to ship until evidence arrives), and, where it applies, a **measurable trigger** (the signal that converts the question to a decision). Each is tagged **[Technical — settled]** (engineering judgment resolves it; the trigger tells you when to act) or **[Market — needs validation]** (the answer is empirical; the default is a hedge, not a conclusion). The technical resolutions are safe to design against; the market ones must not be treated as settled until design-partner data confirms them.

**Q1 — Wedge connector minimum. [Market — needs validation]**
*Question:* can a first deal land on tickets+docs alone, or is Gong/Salesforce read access required for credible customer-voice claims?
*Recommended resolution:* the wedge must prove its 30-day ROI on the **lowest-trust connector set**; if it needs Gong/Salesforce to work, the sales cycle lengthens exactly where mid-market deals can least afford it.
*Default:* make tickets + docs (Zendesk/Jira + Notion/Confluence) sufficient for the value proof; treat Gong/Salesforce as coverage *upgrades* surfaced honestly ("+ Gong would raise customer-voice coverage 41% → 78%"), never as requirements.
*Validation:* the first 5 design-partner deals — track whether any closed without Gong/Salesforce and whether the wedge proof held on tickets+docs alone.

**Q2 — P4 (engineering) quality threshold. [Market — needs validation, with an engineering instrument]**
*Question:* what edit-distance / approval-latency band converts engineering leads from veto to advocate?
*Recommended resolution:* the band is empirical per org, but the *instrument* is fixed now — measure normalized edit-distance on accepted stories and time-to-approval per (team, task-type), per the eval harness.
*Default:* operate to the Discovery kill/pivot trigger — if generated stories exceed **30% edit-distance after two quarters of tuning**, stop expanding scope and fix quality. Use that 30% as the provisional advocacy ceiling until per-tenant data narrows it.
*Validation:* correlate edit-distance/approval-latency against an explicit advocate/neutral/veto survey of engineering leads across the first cohort; the crossover point is the real threshold.

**Q3 — Decision Ledger adoption. [Technical — settled]**
*Question:* does the ledger adopt organically, or require a ritual that implies the services motion we've sworn off?
*Recommended resolution:* it adopts organically **if and only if** ledger entries are a *byproduct of actions people already take*, never a separate form. A commit ceremony fires from approving a PRD or committing an Arena ranking; the decision object is assembled from what the human did. The Archivist agent proposes `DecisionCandidate`s mined from Slack/meetings so the ledger fills itself.
*Default:* ship zero standalone "log a decision" forms. If adoption still lags, that is a UX failure to fix in the product, not a reason to add services.
*Trigger:* if, after a quarter, <40% of committed decisions originate as a byproduct of an existing action (rather than manual authoring), treat it as a product-design defect and redesign the commit surfaces — do not introduce a decision-review ritual that requires onboarding services.

**Q4 — Consumption pricing acceptance. [Market — needs validation]**
*Question:* will 2026 product orgs accept metered AI work, or does budget predictability force bundling?
*Recommended resolution:* don't pick for the customer; the architecture already meters every autonomy unit, so offering both costs nothing to test.
*Default:* present a predictable **platform fee** (procurement-friendly) *and* metered **autonomy units** side by side; let the first cohort choose. The split they pick is the answer.
*Validation:* the platform-fee-vs-consumption mix chosen across the first ~10 design-partner contracts; if >70% refuse metering, bundle by default and expose consumption only as an enterprise option.

**Q5 — Outcome-tier analytic rigor. [Mixed — engineering floor settled, CFO acceptance needs validation]**
*Question:* the minimum analytic rigor (short of full causal inference) a CFO accepts for SLA'd outcome pricing.
*Recommended resolution:* set a defensible **engineering floor now** — pre-registered prediction + defined measurement window + a holdout or difference-in-differences where feasible, with honest uncertainty bands and reported misses (the "narrative honesty" metric). That is below full causal inference but above "trust us."
*Default:* sell the outcome tier only against metrics that clear this floor; refuse SLAs on metrics that can't be instrumented to it ("unmeasurable as predicted" is surfaced, never hidden).
*Validation:* whether target-segment CFOs accept the floor in outcome-tier negotiations; raise rigor per segment only where a specific CFO rejects it.

**Q6 — Generic-graph trigger (TD-6). [Technical — settled]**
*Question:* what tenant topology/heterogeneity forces the move from the relational hierarchy to the full bitemporal typed graph?
*Recommended resolution:* migrate when the polymorphic escape hatch becomes the main road — i.e. when the fixed Product→Feature→Epic→Story tree can no longer express a tenant's real relationships without routing them through `(context_type, context_id)`.
*Default:* stay on the relational hierarchy through Year-1; instrument the ratio of polymorphic-link rows to strict-FK rows per tenant from day one.
*Trigger:* when that ratio crosses **~1:4** on the three largest tenants, build the typed edge/provenance graph **layered over** the relational tables (do not migrate the tables). Expected Year-2; building it earlier is premature.

**Q7 — Orchestration trigger / Temporal adoption (TD-4). [Technical — settled]**
*Question:* at what saga-complexity / replay-volume does Temporal beat the NestJS + BullMQ orchestrator?
*Recommended resolution:* Temporal earns its place at the first of two events — (a) an agent run routinely spans **≥3 external mutations requiring rollback** (true multi-system saga compensation, e.g. a half-succeeded Jira epic-tree push), or (b) **arbitrary-checkpoint run replay** becomes contractual for audit disputes. BullMQ expresses neither cheaply.
*Default:* NestJS orchestrator + BullMQ through Year-1; keep runs stateless and checkpointed so the swap is mechanical, not a rewrite.
*Trigger:* adopt Temporal the quarter either (a) or (b) first appears — expected to coincide with **L3/L4 autonomy GA (Year-2)**, when audit-replay becomes a contractual requirement.

**Q8 — Lexical-engine trigger / OpenSearch (TD-8). [Technical — settled]**
*Question:* does large-tenant exact-match/ID recall require a dedicated lexical engine, and at what scale?
*Recommended resolution:* decide by measurement, not philosophy. Qdrant's native sparse+dense hybrid handles exact-match (IDs like `PROJ-4112`, error strings) well to roughly **10⁷ chunks**.
*Default:* Qdrant hybrid only; stand up a BM25 exact-match **golden set per large tenant now** so the day you need OpenSearch announces itself.
*Trigger:* add OpenSearch only when exact-match recall against that golden set drops below **~0.95** on your largest tenant *and* the reranker can't recover it. Likely never for mid-market; possibly Year-3 for whale tenants.

---

*End of constitution. Amendments are themselves decisions: any change to this document is recorded with author, rationale, and date — PMOS is self-hosting.*
