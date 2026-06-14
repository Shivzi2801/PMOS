# PMOS — Engineering Decisions

### The Architectural Defense Record · Every Major Technical Decision, Justified

**Status:** Engineering Decisions Record v1.0
**Audience:** Product Managers, Architects, Engineering Leads, anyone who must defend PMOS's technical choices to a buyer, a board, a security reviewer, or an interview panel
**Source of truth:** `PMOS_MASTER_SPEC_Final.md` (Constitution v1.0) and its binding Technical Decisions (TD-1…TD-9), plus `Backend_Modules.md`, `Feature_Inventory.md`, and `API_Design.md`.

---

## 0. How to read this document

This is a **defense record**. Its purpose is not to re-explain how PMOS works but to make every major engineering decision *defensible* — to a CPO buyer worried about lock-in, a CFO worried about cost, a security reviewer worried about leakage, and an interviewer probing for depth.

Every decision below uses the same eleven-part template:

> **Decision · Problem Being Solved · Alternatives Considered · Why Alternatives Were Rejected · Chosen Solution · Benefits · Tradeoffs · Scalability Impact · Security Impact · Cost Impact · Product Impact**

Two framing rules from the Constitution govern everything here:

1. **Vision invariant vs. phased implementation.** Several decisions are deliberately phased: a *logical invariant* is fixed now, and a *Year-1 implementation* ships against it, with a *named end-state* it must not foreclose. Where that is true, the decision states the invariant, what ships now, and the measurable trigger that converts the question into a build.
2. **Claims, not strings.** Every AI-generated prose field is a `Claim[]` (`{text, citations[], kind, confidence}`) at the wire level. This is a protocol, not a convention, and it underlies several decisions below.

---

# Part I — The Decisions

---

## 1. PostgreSQL as the System of Record

**Decision.** PostgreSQL 16 is the single system of record for PMOS: the product hierarchy, the decision ledger, the agent/audit spine, and the source of every derived index. Everything else (vectors, caches, search) is a *projection* that must be rebuildable from Postgres.

**Problem Being Solved.** PMOS sells itself as the *system of decision* — "git for product decisions." That promise requires one authoritative, transactionally consistent, auditable store of truth. If the truth is smeared across several stores with no clear owner, provenance becomes unverifiable, audit becomes impossible, and the core product claim collapses.

**Alternatives Considered.**
- A document database (MongoDB/DynamoDB) as primary store.
- A graph-native database (Neo4j/Neptune) as primary store, matching the conceptual Product Knowledge Graph.
- A "polyglot persistence" model where each datastore is independently authoritative for its slice.
- pgvector inside Postgres to also serve as the vector store.

**Why Alternatives Were Rejected.**
- *Document DBs* weaken the transactional guarantees that the transactional outbox and hash-chained audit depend on; you cannot write state and the event that announces it in one atomic transaction across a weakly-consistent store.
- *Graph-native DBs* model the conceptual PKG well but trade away mature OLTP, RLS, mature tooling, and the relational integrity PMs and agents actually reason in (Product → Feature → Epic → Story). The spec resolves this by keeping the relational schema as the system of record and treating the typed graph as a *conceptual/queryable projection* layered over it (TD-6).
- *Polyglot-authoritative* designs make provenance non-resolvable: there is no single place "what we decided and why" lives.
- *pgvector* was explicitly considered and rejected for the vector role because heavily-filtered retrieval, native dense+sparse hybrid, and OLTP contention are better served by a dedicated vector engine (see §2).

**Chosen Solution.** PostgreSQL 16 holds all canonical state. UUIDv7 primary keys everywhere (time-ordered, and they double as the Qdrant point ID so there is no mapping table). Conventions are uniform: `timestamptz` always, `updated_at` by trigger, soft delete via `deleted_at` (30-day trash → purge = the GDPR erasure path). Vectors are *derived* and always regenerable; the Postgres↔Qdrant contract is the `document_chunks` table, where the chunk's Postgres `id` is the Qdrant point ID and `content_hash` prevents redundant re-embedding.

**Benefits.** One authoritative truth; full transactional integrity for the outbox and audit chain; mature RLS for tenancy; clean keys and conventions that reduce every downstream cost; any index can be rebuilt from the record in minutes.

**Tradeoffs.** A relational store is not a native graph, so the richest graph queries are served by a projection rather than the base tables — accepted explicitly (TD-6). Postgres also becomes a critical scaling pole that must be partitioned/sharded carefully at the high end.

**Scalability Impact.** `organization_id` and `workspace_id` on every content row make the table a ready-made shard key. Read load is absorbed by precomputed projections and caches, not by hammering Postgres. Derived indexes rebuild from the event archive, so scaling reads does not threaten the record.

**Security Impact.** Centralizing truth in one RLS-capable store is what makes the existential cross-tenant isolation guarantee enforceable at the data layer (see §15–16). Append-only audit tables with `UPDATE`/`DELETE` revoked at the role level live here.

**Cost Impact.** Avoids the operational cost and consistency-reconciliation tax of multiple authoritative stores. The marginal cost of a read viewer is a cache hit, not a Postgres query — which is what makes "unlimited free viewers" affordable on purpose.

**Product Impact.** The schema *is* the compounding asset per tenant. Because truth is singular and auditable, every generated sentence can be traced to a resolvable source — the product's central trust claim.

---

## 2. Qdrant as the Vector Store

**Decision.** Qdrant is the dedicated vector store for semantic retrieval, holding *derived* embeddings only — nothing stored in Qdrant that cannot be regenerated from Postgres.

**Problem Being Solved.** GraphRAG retrieval needs fast, heavily-filtered semantic search over `10⁸` chunks per large tenant, with hard tenant isolation on every query and native hybrid (dense + sparse) ranking — without dragging that load onto the OLTP store.

**Alternatives Considered.**
- pgvector inside the primary Postgres.
- A managed cloud vector service (e.g. Pinecone).
- Standing up OpenSearch/Elasticsearch as the primary semantic+lexical engine from day one.

**Why Alternatives Were Rejected.**
- *pgvector* loses on three counts named in the spec: heavily-filtered retrieval performance, lack of native dense+sparse hybrid, and OLTP contention on the system of record. Mixing high-QPS vector search into the transactional store endangers the record's latency budgets.
- *Managed vector services* would weaken the tenant-isolation story (force-injected `organization_id` payload filtering and per-tenant collections at scale) and add a third-party dependency on the hot retrieval path with weaker control over residency and cost.
- *OpenSearch as primary* is heavier than needed for Year-1: Qdrant's native hybrid handles exact-match well to roughly `10⁷` chunks, so OpenSearch is deferred to an optional at-scale leg (TD-8) rather than a day-one requirement.

**Chosen Solution.** Qdrant with payload-partitioned, single-collection-per-granularity in Year-1; `organization_id` is an `is_tenant`-indexed payload field and the org filter is force-injected from context, never from request params. Qdrant is reachable *only* from the retrieval service. Collections are dimension-fixed; a model change is a blue/green collection swap. A nightly reconciler heals any Postgres↔Qdrant drift.

**Benefits.** Purpose-built filtered ANN performance; native hybrid ranking without a second engine; clean tenant isolation via force-injected payload filters; vectors are disposable and rebuildable.

**Tradeoffs.** A second datastore to operate and reconcile. Dimension-fixed collections mean an embedding-model change is a managed migration (blue/green), not a hot edit.

**Scalability Impact.** Payload partitioning Year-1; per-tenant collections (fail-closed) at cell scale. int8 quantization and tiered search (coarse pre-filter → rescore → cross-encoder → LLM-select) keep retrieval affordable at `10⁸` chunks.

**Security Impact.** Reachable only from the retrieval service and only with a force-injected org filter — a request can never widen its own tenant scope. At cell scale, per-tenant collections add a physical isolation layer.

**Cost Impact.** Quantization and tiered cascades mean most retrieval never touches the most expensive stage; ~70% of ingested records never touch an LLM at all. Keeping vectors off Postgres protects the OLTP cost/latency budget.

**Product Impact.** Sub-second, permission-safe semantic recall is what makes Ask-the-Brain and evidence-native generation feel instant and trustworthy.

---

## 3. GraphRAG (Typed-Graph Retrieval)

**Decision.** Retrieval is **Hybrid GraphRAG**: vector + lexical + *typed graph traversal* + governed metric-store calls + ledger lookups, fused and reranked, then ACL-trimmed before generation.

**Problem Being Solved.** Plain semantic search returns topically-similar text but cannot answer relational, causal questions — "why don't we support SSO on Starter, what evidence drove it, who owns it, when is it reviewed?" Those answers live in the *relationships* between decisions, evidence, owners, and outcomes, not in any single chunk.

**Alternatives Considered.**
- Pure vector RAG (semantic similarity only).
- Pure keyword/lexical retrieval.
- A monolithic prompt-stuffing approach (dump everything into context).

**Why Alternatives Were Rejected.**
- *Pure vector RAG* misses exact identifiers (e.g. `PROJ-4112`), misses the typed relationships that make a decision explicable, and cannot enforce that numbers come from a governed source.
- *Pure lexical* misses semantic paraphrase and cross-document synthesis.
- *Prompt-stuffing* breaks tenant ACL trimming, blows context budgets, and makes provenance unverifiable.

**Chosen Solution.** Parallel retrieval legs are fused and reranked; the typed-graph leg traverses the provenance/link tables that project the conceptual PKG over the relational hierarchy; **numbers are tools, not text** — any quantitative claim must originate from a governed metric-store call; generation is claim-grounded and then groundedness-verified.

**Benefits.** Answers that are relational and causal, not just topical; every number is governed; every sentence is citable; confirmation bias is fought structurally via a mandatory contrarian probe ("evidence against").

**Tradeoffs.** More moving parts and more latency than single-leg retrieval; fusion/reranking must be tuned per tenant; the typed-graph projection must be maintained alongside the relational tables.

**Scalability Impact.** Tiered search and per-tenant reranker heads (base embedder untouched) keep cost bounded as corpus grows. The graph leg is a projection, so it scales with index rebuilds rather than schema migrations.

**Security Impact.** ACL trimming happens *pre-fusion* using each chunk's inherited `read_principals`; an answer computed without evidence the asker cannot see renders "n sources withheld by permissions" — honest, never leaking, never silently omitting the fact of omission.

**Cost Impact.** Cheap-first cascades mean the expensive LLM-select stage runs on a small fraction of candidates; governed metric calls avoid paying an LLM to "guess" numbers.

**Product Impact.** This is the machinery behind the Org-Wide Product Brain, the Contradiction Engine, and evidence-native artifacts — features structurally unavailable to a document-generator because they require the graph to exist.

---

## 4. RAG with Claim-Grounded Generation

**Decision.** All AI prose is generated via retrieval-augmented generation where every sentence is grounded in retrieved evidence and emitted as a `Claim[]` with sentence-level citations; uncitable sentences are marked `inference` and rendered differently.

**Problem Being Solved.** "AI slop" — confident, uncited, ungrounded generated text — is the exact thing that triggers the engineering lead's veto (persona P4) and destroys a decision system's credibility. A decision record you cannot trust is worse than none.

**Alternatives Considered.**
- Free-form LLM generation with a post-hoc "citations panel."
- Fine-tuning a model per tenant to "know" the corpus instead of retrieving.
- Returning prose as plain strings and attaching sources loosely.

**Why Alternatives Were Rejected.**
- *Free-form + citations panel* leaves the body text unverifiable and lets hallucinations through between citations; the spec explicitly bans a separate citations panel and uncited sentences.
- *Per-tenant fine-tuning* bakes stale knowledge into weights, can't honor live ACLs, can't cite, and entangles tenant data with model weights (a cross-tenant and privacy hazard).
- *Plain strings with loose sources* makes provenance unresolvable at the sentence level and breaks the <400ms provenance guarantee.

**Chosen Solution.** RAG over the GraphRAG retrieval set; `Claim[]` is the wire protocol everywhere AI prose appears; a groundedness verification step gates output; the Provenance Underline encodes evidence weight visually (single / corroborated / inference / simulated / degraded).

**Benefits.** Every sentence is a resolvable contract; honesty is structural; the human can audit any claim in <400ms; abstention on unanswerable questions is measurable (honesty metric ≥95% correct abstention).

**Tradeoffs.** Generation is slower and more constrained than free-form; engineering must maintain the verification step and the `Claim[]` plumbing end to end.

**Scalability Impact.** The model is rented and swappable; the learning loops (calibration, per-tenant reranker, anti-pattern memory) are owned and compound. A model upgrade improves output overnight with zero migration.

**Security Impact.** Grounding to ACL-trimmed evidence means generation cannot leak content the asker lacks rights to. Ingested content is treated as hostile and confined to typed evidence blocks (see §16).

**Cost Impact.** Tiered routing sends most tasks to cheaper models; only frontier-class work hits the frontier tier. Governed numbers avoid wasted LLM calls.

**Product Impact.** This is the answer to the P4 veto and the foundation of "every generated sentence is a contract" — the trust that makes autonomy sellable later.

---

## 5. Event Backbone (Transactional Outbox over Redis Streams)

**Decision.** State changes propagate through an event backbone whose **invariant is the transactional outbox**; the Year-1 transport is Redis Streams via a relay worker, with an explicit Kafka/MSK-swappable seam for cell scale (TD-2).

**Problem Being Solved.** "Honest degradation, never silent staleness" is impossible without reliable eventing. Interactive writes must not block on autonomous work, and projections must stay current without dual-write bugs that drop or duplicate events.

**Alternatives Considered.**
- Direct synchronous calls between services.
- Dual-writing to the database and a message bus separately (write state, then publish).
- Kafka/MSK from day one.

**Why Alternatives Were Rejected.**
- *Synchronous calls* couple write latency to downstream work and create cascading failure surfaces.
- *Dual-write* is the classic data-loss bug: the publish can fail after the commit (or vice versa), leaving state and events out of sync — exactly the silent staleness the product forbids.
- *Kafka day one* is operational over-investment for Year-1 volumes; Redis Streams meets the need now, and the outbox makes the eventual swap non-breaking.

**Chosen Solution.** Outbox-only: no event exists without the state change committed in the *same* Postgres transaction. The relay worker reads the outbox and publishes to Redis Streams; transport is swappable because consumers depend on the event contract, not the broker.

**Benefits.** Exactly-once-by-construction event emission; interactive writes decoupled from async work; projections rebuildable in minutes from the event archive; broker swap is mechanical.

**Tradeoffs.** A relay worker to operate and monitor; eventual (not instantaneous) consistency for projections — accepted, and surfaced honestly via freshness badges.

**Scalability Impact.** Fan-out absorbs write growth; CQRS projections are added only where UX budgets demand precomputation (Brief, portfolios, audit timelines). Kafka/MSK is the named upgrade at cell scale.

**Security Impact.** Every state change is paired with an auditable event in the same transaction, supporting the audit fabric's completeness. The outbox rows themselves carry `organization_id` and respect tenancy.

**Cost Impact.** Redis Streams is far cheaper to operate than a managed Kafka cluster at Year-1 scale; the swap is deferred until scale justifies the cost.

**Product Impact.** This is what lets viewers see fresh state cheaply and lets the Overnight PM and ingestion run without ever blocking a human's interactive write.

---

## 6. Transactional Outbox Pattern

**Decision.** The outbox pattern is the *only* sanctioned way a state change becomes an event: a module writes an `outbox` row in the same transaction as the state change, and the relay transports it.

**Problem Being Solved.** Reliability of the event stream itself. Without atomicity between "the thing happened" and "the thing was announced," the entire downstream world (projections, audit, notifications, sync) drifts from reality.

**Alternatives Considered.**
- Application-level publish after commit.
- Change-Data-Capture (CDC) tailing the Postgres WAL.
- Distributed transactions (2PC) across DB and broker.

**Why Alternatives Were Rejected.**
- *Publish-after-commit* reintroduces the dual-write failure window.
- *CDC/WAL tailing* couples the event schema to physical table layout, leaks internal structure, and complicates the domain event contract; the outbox keeps events as a deliberate, versioned domain artifact.
- *2PC* is operationally fragile and slow, and few brokers support it cleanly.

**Chosen Solution.** Same-transaction outbox writes; relay-based delivery; the event contract is the stable interface so the transport underneath (Redis Streams → Kafka/MSK) can change without touching producers or consumers.

**Benefits.** Atomic, lossless eventing; transport independence; a clean, intentional event schema; trivially testable.

**Tradeoffs.** A small write amplification (one extra row per state change) and a relay to operate.

**Scalability Impact.** Outbox throughput scales with Postgres write capacity; the relay scales horizontally; the pattern is the seam that makes the Kafka migration a transport swap rather than a rewrite.

**Security Impact.** Guarantees no auditable action goes unannounced — load-bearing for audit completeness and for the cross-tenant probe to have a complete event picture.

**Cost Impact.** Negligible storage overhead; saves the far larger cost of reconciliation tooling and incident response for dropped events.

**Product Impact.** Underpins "never silent staleness" — the credibility floor for a system of record.

---

## 7. Multi-Agent Architecture (Eleven Specialized Agents)

**Decision.** PMOS's AI is organized as **eleven agents** — three system agents (Conductor, Sentinel, Archivist) plus eight specialists (Strategist, Research, Roadmap, Prioritization, PRD, Story, Analytics, Release) — each a versioned software unit, **stateless between tasks**.

**Problem Being Solved.** Product management is many distinct jobs with different tools, quality bars, and authority needs. A single do-everything agent cannot have task-specific evals, task-specific tool manifests, or task-specific autonomy levels — and cannot be audited or replayed cleanly.

**Alternatives Considered.**
- One monolithic "PM agent" with a giant prompt and all tools.
- A loose swarm of agents with shared mutable memory and emergent coordination.
- Hard-coded workflow scripts with no agent abstraction.

**Why Alternatives Were Rejected.**
- *Monolith* cannot scope authority per task (an L1 drafting capability and an L3 write capability would be indistinguishable), cannot be evaluated per task type, and concentrates blast radius.
- *Loose swarm with shared mutable state* destroys replayability and auditability and invites prompt-injection lateral movement.
- *Hard-coded scripts* cannot adapt, cannot reason over the graph, and cannot improve as models improve.

**Chosen Solution.** Each agent carries a role charter, tool manifest, memory lens, autonomy matrix (held by the policy engine), model binding, eval suite, and KPIs. Agents are stateless — all state lives in the Task Workspace and the memory plane — so **every run is replayable and auditable**. The Conductor handles intake/planning/delegation/assembly; specialists do the work.

**Benefits.** Per-task authority, per-task evals, per-task model routing, contained blast radius, full replay/audit, and clean human-in-the-loop submission boundaries.

**Tradeoffs.** Orchestration complexity (coordination, hand-offs, assembly) and more components to version and evaluate.

**Scalability Impact.** Stateless runs scale horizontally as queue workers; ~3M agent tasks/day platform-wide are absorbed by batch lanes on the Model Gateway without blocking interactive users.

**Security Impact.** Statelessness + per-agent tool manifests + capability tokens mean an agent can only ever do what its current run's token authorizes; an L1 agent literally has no write token to misuse. Injection cannot escalate authority across agents.

**Cost Impact.** Per-task model routing sends cheap work to cheap tiers; only the tasks that need frontier reasoning pay frontier prices. Replayability avoids re-doing work to reconstruct what happened.

**Product Impact.** This is the "AI product team" — each agent maps to a recognizable PM function, which makes capability and autonomy legible to the buyer and earns trust agent-by-agent.

---

## 8. Model Gateway

**Decision.** All model access (LLMs and embeddings) goes through a single **Model Gateway** with per-task tiered routing (frontier / mid / small / embedding) under Zero-Data-Retention (ZDR) contracts. Default agent model is `claude-sonnet-4-6` per `ai_agents.model` (TD-9); embeddings are OpenAI `text-embedding-3-large` @ 3072 dims (TD-5).

**Problem Being Solved.** "The model layer is rented; the learning loops are owned." Models change every few months; tenants demand data-handling guarantees; cost varies 100× across tiers. Calling providers directly from agents would scatter contracts, costs, and routing logic across the codebase and create lock-in.

**Alternatives Considered.**
- Direct provider SDK calls from each agent/service.
- Hard-committing to a single provider and model.
- A self-hosted open model as the default.

**Why Alternatives Were Rejected.**
- *Direct calls* spread ZDR enforcement, retries, cost metering, and routing everywhere — impossible to govern or swap centrally.
- *Single-provider lock-in* contradicts the moat thesis (the moat is the owned loops, not the model) and leaves PMOS exposed to one vendor's pricing and outages.
- *Self-hosted default* trades away frontier quality and adds heavy ops for a capability PMOS deliberately rents.

**Chosen Solution.** One gateway enforces ZDR, meters tokens, routes per task type (hot-swappable policy), and provides model-provider failover. On provider failure, Ask degrades to a lower tier **with a visible quality badge** (honest degradation), never a silent downgrade.

**Benefits.** Centralized ZDR + metering + routing; instant model swaps with zero agent changes; provider failover; cost control by tier; no cross-tenant training by default.

**Tradeoffs.** A central component on the critical path that must be highly available; routing policy must be maintained and tuned.

**Scalability Impact.** Batch lanes separate autonomous work from interactive token streaming; routing keeps the expensive tier reserved for tasks that need it across ~3M tasks/day.

**Security Impact.** Single enforcement point for ZDR contracts and the "no cross-tenant training" guarantee; clients never call the gateway directly (only through the BFF), so model access is never client-reachable.

**Cost Impact.** This is the primary COGS lever — tiered routing and cheap-first cascades are why per-task COGS stays within budget (measured, not estimated). A cheaper frontier model arriving improves margins overnight.

**Product Impact.** Enables "a model upgrade improves PMOS overnight with zero migration," and the honest quality badge keeps trust intact during degradation.

---

## 9. Claims Protocol (`Claim[]`)

**Decision.** Every AI-generated prose field, everywhere in the system, is a `Claim[]` (`{text, citations[], kind, confidence}`) at the wire level — never a bare string.

**Problem Being Solved.** Trust. A decision system of record cannot have sentences that float free of evidence. Provenance must be material, structural, and resolvable, not a bolt-on.

**Alternatives Considered.**
- Plain strings with an optional sources field.
- Markdown with inline footnotes.
- A document-level (not sentence-level) citation model.

**Why Alternatives Were Rejected.**
- *Plain strings* make sentence-level provenance impossible and let hallucinations hide between citations.
- *Markdown footnotes* are presentation, not protocol — they can't be enforced in schemas, validated, or rendered with evidence-weight semantics.
- *Document-level citations* are too coarse: the unit of trust is the sentence, and the contrarian probe and groundedness check operate per claim.

**Chosen Solution.** `Claim[]` is enforced in API response schemas (REST and GraphQL) throughout; each claim carries its citations, a `kind` (e.g. fact vs. inference), and a `confidence`. The Provenance Underline renders evidence weight (single / corroborated / inference / simulated / degraded) with redundant non-color encoding for accessibility.

**Benefits.** Sentence-level auditability; structural honesty; uniform handling across every surface; accessibility-friendly evidence encoding; the foundation for confidence scoring and abstention metrics.

**Tradeoffs.** Every producer and consumer of AI prose must speak the protocol — more plumbing than returning strings.

**Scalability Impact.** A uniform wire type lets every surface and projection handle AI prose identically, which simplifies caching and rendering at scale.

**Security Impact.** Citations resolve only to ACL-trimmed evidence; "n sources withheld by permissions" is expressible within the protocol, so omission is honest and never silent.

**Cost Impact.** Minimal per-payload overhead; saves the much larger cost of trust failures and unverifiable output.

**Product Impact.** This *is* the product's central differentiator made concrete — "every generated sentence is a contract" — and the direct answer to the AI-slop veto.

---

## 10. SSE Streaming

**Decision.** Server-Sent Events carry all server-push: Ask token/claim streaming, agent-run progress, async-job progress, and the Tide. **No WebSockets in Year-1.**

**Problem Being Solved.** Interactive AI needs sub-second first-token feel and live progress for long agent runs, plus a calm live notification stream — all unidirectional server→client. The transport must be resumable, cheap to scale, and simple to secure.

**Alternatives Considered.**
- WebSockets for all real-time traffic.
- Long-polling.
- gRPC streaming.

**Why Alternatives Were Rejected.**
- *WebSockets* add sticky bidirectional sessions and a stateful plane that complicates horizontal scaling, when every Year-1 push is unidirectional. They are reserved for one named future case only (multiplayer canvas presence, Year-3) and would never carry authoritative writes.
- *Long-polling* wastes connections and gives poor first-token latency.
- *gRPC streaming* is awkward for browsers and adds tooling burden for no benefit over SSE here.

**Chosen Solution.** SSE under `/api/v1/...`, resumable via `Last-Event-ID`, 15s heartbeats, server-side termination at 15 min with client resume. Auth: `fetch`-based streaming uses the normal `Authorization` header; native `EventSource` uses a single-use 60-second **stream ticket**, never the session token in a URL.

**Benefits.** Native HTTP/2 multiplexing; trivial resume; stateless BFF; minimal operational surface; clean browser support; secure auth that keeps tokens out of URLs.

**Tradeoffs.** Unidirectional only (acceptable for Year-1); long-lived connections need heartbeats and termination handling.

**Scalability Impact.** Stateless SSE keeps the BFF horizontally scalable; no sticky sessions to pin. ~50,000 concurrent interactive users are served without a stateful socket plane.

**Security Impact.** Single-use 60s stream tickets prevent session-token leakage via query strings; auth stays on the standard header path for `fetch`.

**Cost Impact.** Lower operational cost than a WebSocket fleet; no sticky-session infrastructure.

**Product Impact.** Delivers the <700ms Ask first-token feel and live agent-run visibility that make autonomy legible ("what is the AI doing right now").

---

## 11. BFF Layer (Backend-for-Frontend)

**Decision.** The **BFF is the single client-facing surface**. No client ever calls a domain service, the Model Gateway, Qdrant, or Postgres directly.

**Problem Being Solved.** A decision system exposes many domain services and three protocols; letting clients call them directly would scatter authorization, rate-limiting, idempotency, and tenant-context enforcement across the system and make the attack surface unbounded.

**Alternatives Considered.**
- Clients call domain microservices directly (service mesh exposed to the edge).
- A thin pass-through API gateway with no aggregation.
- A GraphQL-only federated gateway.

**Why Alternatives Were Rejected.**
- *Direct service access* multiplies the places auth/tenancy/idempotency must be enforced and exposes internal topology to attackers.
- *Thin pass-through* doesn't solve lens aggregation (composing ≥3 resources for one screen) and pushes orchestration to the client.
- *GraphQL-only* can't cleanly host idempotent, individually-rate-limited, auditable commands — which the spec keeps on REST by construction.

**Chosen Solution.** One BFF fronts the three-protocol contract (REST commands / GraphQL lenses / SSE streams), enforces TenantContext, authorization, idempotency, rate limits, and versioning in one place, and is the only thing internal services trust from the edge. The frontend ships a dev fixture implementing the exact `/api/v1` contract; flipping `NEXT_PUBLIC_PMOS_API_URL` to a real BFF swaps it transparently.

**Benefits.** One place to enforce every cross-cutting concern; internal topology hidden; lens aggregation server-side; frontend can develop against a fixture with an identical contract.

**Tradeoffs.** The BFF is a critical path that must be highly available and must not become a god-object; discipline is required to keep domain logic in domain services.

**Scalability Impact.** Stateless and horizontally scalable; absorbs read fan-out via cached projections; the single choke point is also the single place to apply backpressure and kill switches.

**Security Impact.** The single enforcement point for authorization, tenant context, and rate limiting; reduces the attack surface to one audited surface; clients can never reach a datastore or the model layer.

**Cost Impact.** Centralized caching and rate-limiting reduce backend load; one surface to secure and monitor instead of many.

**Product Impact.** Enables the "one surface, many lenses; never many apps in a trenchcoat" design law and a consistent client contract.

---

## 12. NestJS (Backend Framework)

**Decision.** All backend services are built on **NestJS 10** (TypeScript).

**Problem Being Solved.** PMOS needs a single, structured backend framework that supports modular services, dependency injection, multiple protocols (REST/GraphQL/SSE), and shared TypeScript types with the frontend — without inventing house conventions for every module.

**Alternatives Considered.**
- A Python backend (FastAPI/Django) to sit close to the AI/ML ecosystem.
- A Go backend for raw performance.
- Bare Express/Fastify with hand-rolled structure.

**Why Alternatives Were Rejected.**
- *Python* would split the stack's type system from the Next.js frontend, duplicate models, and lose end-to-end TypeScript sharing; the heavy ML work is rented behind the Model Gateway anyway, so co-locating with Python buys little.
- *Go* maximizes throughput but costs developer velocity, a unified type system with the frontend, and the rich DI/module ecosystem NestJS provides.
- *Bare Express* leaves every team reinventing module boundaries, DI, and validation — the opposite of the spec's "every module answers the same questions" discipline.

**Chosen Solution.** NestJS 10 for every backend service: opinionated modules map cleanly to the module specification, first-class support for REST/GraphQL/SSE, DI for testability, and shared TypeScript types with the Next.js client.

**Benefits.** Consistent module structure across the whole backend; one language end-to-end; strong DI and testing story; native support for all three protocols.

**Tradeoffs.** Node's single-threaded model needs care for CPU-bound work (offloaded to workers/queues); some opinionation to live with.

**Scalability Impact.** Services run as horizontally-scalable replicas (Year-1 horizontal unit); CPU-heavy work is pushed to BullMQ workers, keeping request handlers light.

**Security Impact.** Uniform middleware (TenantContext enforcement, RLS context, auth) applied consistently because every service shares the framework's request pipeline.

**Cost Impact.** One language and one framework lowers hiring, onboarding, and maintenance cost; shared types cut integration defects.

**Product Impact.** Faster, more consistent delivery of the large module surface; the structural uniformity is what lets the build sequence be planned module-by-module.

---

## 13. Next.js (Frontend Framework)

**Decision.** The frontend is **Next.js** (App Router, React Server Components, deployed on Vercel).

**Problem Being Solved.** PMOS's UI is a single canvas with many lenses, strict interactive performance budgets (cold load to interactive Brief <1.5s, canvas ≥60fps), heavy reads served from precomputed projections, and live AI streaming — all needing fast first paint and server-side data composition.

**Alternatives Considered.**
- A pure client-side SPA (Create-React-App/Vite).
- A different SSR meta-framework (Remix/SvelteKit).
- A non-React stack.

**Why Alternatives Were Rejected.**
- *Pure SPA* ships too much to the client, hurting the <1.5s cold-load budget and SEO/first-paint for read-mostly viewers (≈92% of users).
- *Alternate meta-frameworks* are viable but would not share the NestJS/TypeScript type ecosystem as cleanly, and RSC fits the projection-served read model directly.
- *Non-React* loses the largest component ecosystem and the team's velocity.

**Chosen Solution.** Next.js App Router with RSC for server-composed reads (lenses rendered from projections), client components for the interactive canvas and the Line, and SSE for live AI streaming. The built-in dev fixture lets the frontend develop against the exact API contract.

**Benefits.** Fast first paint and server composition; RSC matches the projection read model; shared TypeScript types with the backend; mature ecosystem; clean Vercel deployment.

**Tradeoffs.** App Router/RSC complexity; careful client/server boundary management for the interactive canvas.

**Scalability Impact.** Read-mostly viewers hit cached projections rendered server-side at near-zero marginal cost — the architectural reason "unlimited viewers" is cheap.

**Security Impact.** Server-side data fetching keeps tenant-scoped access on the server through the BFF; the client never holds datastore credentials.

**Cost Impact.** Server rendering from projections minimizes per-viewer compute; Vercel scales reads cheaply.

**Product Impact.** Hits the interactive performance budgets that make the "calm authority" experience feel instant; enables "the system speaks first" with server-rendered findings.

---

## 14. Clerk (Identity)

**Decision.** Identity is **Clerk** — SSO (SAML/OIDC), MFA for editor+ roles, SCIM (enterprise), JWKS-verified JWTs. **Passwords never touch PMOS endpoints** (TD-7).

**Problem Being Solved.** Enterprises require federated identity and refuse to let yet another vendor hold their credentials; credential theft is threat #3. Building auth in-house is high-risk, low-differentiation work.

**Alternatives Considered.**
- Build authentication in-house (password storage, SSO, MFA, SCIM).
- A different IdP/auth platform (Auth0, Okta, Cognito).
- Storing an `auth_provider` of `password` in the database (as an early DB draft proposed).

**Why Alternatives Were Rejected.**
- *In-house auth* means owning password storage, SSO protocol edge cases, MFA, and SCIM — enormous surface, zero differentiation, large breach liability.
- *Other IdPs* are viable; Clerk is chosen as canonical, and the architecture keeps authority in PMOS policy data so the IdP remains a swappable identity source, not a lock-in.
- *Storing passwords* is explicitly superseded: the `auth_provider` enum is retained only as an identity-source *label* populated from Clerk, never a credential store (TD-7).

**Chosen Solution.** Clerk is canonical identity; PMOS verifies JWKS-signed JWTs and binds them to TenantContext. Crucially, **identity lives in Clerk but authority lives in PMOS policy data** — a human's authority is computed as RBAC × ABAC × source-ACL trim at request time, and an agent's authority is a capability token. PMOS never stores or sees a password.

**Benefits.** Enterprise-grade SSO/MFA/SCIM with no credential custody; eliminates a whole class of credential-theft risk; unblocks enterprise procurement; fast to ship.

**Tradeoffs.** A third-party dependency on the auth path; some configuration coupling to Clerk's model (mitigated by keeping authority in PMOS).

**Scalability Impact.** Offloads auth scaling to a specialist provider; JWTs are stateless to verify, so the BFF stays stateless.

**Security Impact.** Passwords never touch PMOS endpoints; MFA enforced for editor+ roles; federated identity reduces attack surface; authority decisions remain inside PMOS so the IdP cannot grant PMOS-internal capabilities.

**Cost Impact.** Avoids the build-and-maintain cost (and breach risk cost) of in-house auth; a predictable vendor fee instead.

**Product Impact.** Makes the enterprise sale possible and keeps the human/agent two-principal security model clean.

---

## 15. Redis (Cache, Streams, and Queue Substrate)

**Decision.** **Redis** (via ioredis) serves three roles: read-through cache, the Year-1 event transport (Redis Streams), and the substrate for the job queues (BullMQ).

**Problem Being Solved.** The interactive performance budgets (Peek <100ms, Sheet cached <150ms) require a fast cache; the event backbone needs a Year-1 transport; and autonomous work needs durable queues — ideally without standing up three separate systems on day one.

**Alternatives Considered.**
- Memcached for caching only, plus separate systems for streams and queues.
- Kafka for streaming + a separate queue system.
- A managed cloud cache/queue per role.

**Why Alternatives Were Rejected.**
- *Memcached* is cache-only and can't serve the streams or queue roles, multiplying infrastructure.
- *Kafka + separate queue* is heavier than Year-1 needs (the outbox makes Kafka a deferred upgrade, TD-2).
- *Separate managed service per role* multiplies operational and cost surface for capabilities Redis covers together at Year-1 scale.

**Chosen Solution.** One Redis deployment covers caching, Redis Streams transport (behind the outbox invariant), and BullMQ. Projections and hot reads are cached; the event contract sits above the transport so Streams can later be swapped for Kafka/MSK without touching producers/consumers.

**Benefits.** One well-understood system covering three needs; very low read latency; durable enough for Year-1 streaming and queues; clean upgrade seam.

**Tradeoffs.** Redis Streams is not Kafka — it is the deliberate Year-1 choice with a named upgrade trigger; Redis must be sized and made HA.

**Scalability Impact.** Read-through caches are the mechanism that makes viewer reads marginal-cost-≈-cache-hit; Streams/queues scale to Year-1 volumes, with Kafka/MSK as the cell-scale upgrade.

**Security Impact.** Cached projections and queue payloads carry `organization_id`; Redis is an internal datastore never reachable by clients (only via the BFF/services).

**Cost Impact.** One system instead of three at Year-1 scale; caching slashes Postgres read load and therefore cost.

**Product Impact.** Delivers the sub-150ms cached-read feel and the decoupling that keeps autonomous work from ever blocking a human.

---

## 16. BullMQ (Job Queues)

**Decision.** Autonomous and slow work runs on **BullMQ** queues (on Redis): ingestion lanes, agent tasks, the Overnight shift, exports, imports, backfills, simulations.

**Problem Being Solved.** Slow and autonomous work must never block interactive users, must be prioritizable (live vs. standard vs. bulk), retryable, and observable — under ~50M ingestion events/day and ~3M agent tasks/day.

**Alternatives Considered.**
- Run async work inline in request handlers.
- Temporal as the durable orchestration engine from day one.
- A managed queue (SQS) instead of BullMQ.

**Why Alternatives Were Rejected.**
- *Inline async* blocks users and couples request latency to heavy work.
- *Temporal day one* is over-investment for Year-1; the logical run contract (stateless, checkpointed, replayable) is the invariant, and Temporal is the named target adopted only when saga complexity or contractual audit-replay warrants it (TD-4).
- *SQS* lacks the rich priority/retry/observability features BullMQ gives on the Redis already in the stack, and would add another managed dependency.

**Chosen Solution.** BullMQ on Redis with three ingestion priority lanes (live ≤2 min / standard ≤15 min / bulk best-effort) so backfills never delay live contradiction detection. Agent runs are queued jobs; runs are kept stateless and checkpointed so the eventual Temporal swap is mechanical, not a rewrite.

**Benefits.** Priority lanes, retries, concurrency control, observability; reuses the in-stack Redis; the checkpointed-run contract makes the Temporal migration non-breaking.

**Tradeoffs.** Not a durable saga engine — multi-system rollback and arbitrary-checkpoint replay are the explicit triggers to move to Temporal (TD-4); BullMQ workers must be monitored and scaled.

**Scalability Impact.** Workers scale horizontally; priority lanes protect the live path; batch lanes feed the Model Gateway without starving interactive token streaming.

**Security Impact.** Workers that run `BYPASSRLS` are code-review-required to filter `organization_id` explicitly; job payloads carry tenant context.

**Cost Impact.** Reuses existing Redis; defers the operational cost of Temporal until scale justifies it.

**Product Impact.** Powers the Overnight PM, ingestion freshness lanes, and every async-job-grammar operation — the autonomous work that makes "every day starts at review and decide" real.

---

## 17. Multi-Tenancy (Shared-Schema, organization_id on Every Row)

**Decision.** Year-1 tenancy is **shared database, shared schema**, with `organization_id` (and `workspace_id`) on **every content row**; the named end-state is a **cell-based architecture** (Year-2+) that this design must not foreclose (TD-1).

**Problem Being Solved.** Customers will not trust a decision system of record that could leak data to a competitor — cross-tenant exposure is the existential threat. The design must be safe now and migrate to physical isolation later without a rewrite.

**Alternatives Considered.**
- Database-per-tenant (or schema-per-tenant) from day one.
- A single shared store with only application-layer tenant filtering (no RLS).
- Cell-based physical isolation immediately.

**Why Alternatives Were Rejected.**
- *DB/schema-per-tenant day one* is operationally crushing at ~2,500 tenants and makes cross-tenant features (and migrations) expensive, with no Year-1 payoff over RLS.
- *App-layer-only filtering* is one forgotten `WHERE` clause away from a leak — insufficient for an existential threat.
- *Cells immediately* is premature spend; the spec ships cells as the staging mechanism and Year-2 target, reachable by event-replay + router-flip precisely because `organization_id` is already on every row.

**Chosen Solution.** Shared-schema with PostgreSQL RLS as the hard org boundary; `organization_id` on every row doubles as RLS key, shard key, and Qdrant payload filter. Workspace is a softer app-layer boundary (join-based RLS would degrade query plans — a deliberate defense-in-depth split). The cell stamp ships day one (it is how staging works), so migration is replay + router-flip, not a rewrite.

**Benefits.** Cheap and operable now; migration to cells is mechanical; one column is simultaneously isolation key, shard key, and vector filter; makes "unlimited free viewers" architecturally cheap.

**Tradeoffs.** Shared infrastructure means a tenant's noisy load can affect neighbors until cells arrive (mitigated by the whale-tenant dedicated-cell escape hatch); RLS discipline is mandatory.

**Scalability Impact.** Holds the `10⁷` records / `10⁸` chunks envelope per large tenant; whale tenants trigger dedicated cells/shards; Year-2 cells give blast radius ≤4% of platform with ~2h cell creation via Terraform.

**Security Impact.** The hard boundary plus the cross-tenant probe (any hit = sev-0). The design intentionally requires *three independent enforcement layers to all fail* for a leak (RLS, force-injected Qdrant filter, app-layer checks).

**Cost Impact.** Far cheaper than per-tenant infrastructure at Year-1 scale; defers cell spend until enterprise demand justifies it.

**Product Impact.** Is the precondition for any enterprise sale and for the free-unlimited-viewers expansion engine.

---

## 18. Row-Level Security (RLS)

**Decision.** PostgreSQL **Row-Level Security** with `FORCE ROW LEVEL SECURITY` is the load-bearing data-layer enforcement of tenant isolation; the data layer **refuses any query lacking a TenantContext**.

**Problem Being Solved.** Application-layer filtering alone is too fragile for an existential threat — a single missing predicate leaks data. Isolation must be enforced at the database itself, below the application's mistakes.

**Alternatives Considered.**
- Application-layer `WHERE organization_id = ?` on every query.
- An ORM-level global filter.
- Separate physical databases per tenant.

**Why Alternatives Were Rejected.**
- *App-layer filtering* depends on every developer remembering every predicate forever — unacceptable for the #1 threat.
- *ORM global filters* can be bypassed by raw queries and are not enforced by the database itself.
- *Physical DBs per tenant* is the deferred cell approach, over-costly for Year-1.

**Chosen Solution.** A canonical RLS policy applied to every content table with `FORCE ROW LEVEL SECURITY`; per-request `SET LOCAL app.current_org_id` inside a tenant-scoped transaction (PgBouncer transaction-pooling safe). Middleware refuses context-less queries. Background workers that run `BYPASSRLS` are code-review-required to filter `organization_id` explicitly. RLS is one of the three independent layers (with the force-injected Qdrant filter and app-layer checks).

**Benefits.** Isolation enforced by the database regardless of application bugs; uniform policy across all tables; compatible with connection pooling; defense-in-depth.

**Tradeoffs.** Join-based RLS degrades plans, so workspace isolation is deliberately handled at the app layer; RLS adds a small per-query overhead and requires the `SET LOCAL` discipline.

**Scalability Impact.** Works within shared-schema at Year-1 scale; `organization_id` indexing keeps RLS-filtered queries efficient; the same key shards cleanly at cell scale.

**Security Impact.** The primary technical control against the existential cross-tenant threat; combined with nightly cross-tenant probes, a single layer's failure does not produce a leak.

**Cost Impact.** Near-zero added infrastructure; the cost is engineering discipline, far cheaper than per-tenant isolation or a breach.

**Product Impact.** The credibility floor for selling a shared-tenant decision system of record to security-conscious buyers.

---

## 19. Audit Fabric (Hash-Chained Append-Only Records)

**Decision.** One audit pattern across all auditable surfaces: **append-only Postgres tables**, each row `row_hash = H(prev_hash ‖ canonical_payload)`, with hourly chain-head anchors to WORM S3 and full OpenTelemetry trace linkage. `UPDATE`/`DELETE` are revoked at the role level.

**Problem Being Solved.** Institutional amnesia and audit-record tampering (threat #5). A decision ledger, autonomy log, and billing meters are worthless if they can be silently edited; the org must be able to prove what was decided, by whom, under what authority, and when.

**Alternatives Considered.**
- Mutable audit tables with application-enforced immutability.
- A separate blockchain/DLT for tamper evidence.
- Logging to an external SIEM only.

**Why Alternatives Were Rejected.**
- *Mutable tables* can be altered by anyone with write access or a SQL bug — not tamper-evident.
- *Blockchain/DLT* is operational overkill; a hash chain anchored to WORM storage gives the same tamper-evidence with far less complexity.
- *SIEM-only* is for security logs, not first-class product records (the ledger must be queryable product state, not just an ops log).

**Chosen Solution.** A single hash-chained, append-only pattern reused everywhere auditable (decision ledger, autonomy log, billing meters). Hourly chain-head anchoring to WORM S3 makes retroactive tampering detectable; revoking `UPDATE`/`DELETE` at the role level prevents it; OTel linkage ties records to traces. Deliberately the *same* machinery is the "insurance-grade audit" sold in Year-3 and the surface security reviews inspect.

**Benefits.** Tamper-evident by construction; one pattern to build, test, and review; doubles as a sellable Year-3 capability and a security-review asset.

**Tradeoffs.** Append-only means corrections are new entries, not edits (correct, but a mindset shift); GDPR erasure is handled via tombstones leaving typed "redacted" stubs so auditability survives content removal.

**Scalability Impact.** Append-only writes scale with Postgres; hourly anchoring batches cheaply; the same pattern scales to all auditable tables without per-table design.

**Security Impact.** Directly counters audit-tampering (threat #5); chain + WORM anchor makes any alteration provable; supports SOC 2 / ISO 27001 evidence.

**Cost Impact.** Modest storage and WORM cost; reuse of one pattern avoids bespoke audit builds; underpins a Year-3 revenue line (insurance-grade audit).

**Product Impact.** This is the Decision Ledger's integrity guarantee — "git for product decisions" you can actually trust in a dispute — and the GDPR-compliant erasure path that keeps ledger auditability intact.

---

## 20. Human-in-the-Loop (Approval-Gated Autonomy)

**Decision.** AI output is always a **draft submitted to a human**; Year-1 autonomy is capped at L1–L2, where L2 actions require human approval endpoints and every agent write carries `ai_generated=true` + `source_run_id` (TD-3). No irreversible decision ships without human sign-off.

**Problem Being Solved.** Enterprises will not grant autonomy to an unproven system, and the product's positioning is "PMOS does the management; your people do the leadership." Trust must be *earned*, and authority must be enforced in the runtime, never assumed.

**Alternatives Considered.**
- Full autonomy from launch.
- Autonomy enforced by prompt instructions ("please ask before acting").
- No autonomy at all (pure assistant).

**Why Alternatives Were Rejected.**
- *Full autonomy* would be rejected by buyers and is reckless before measured accuracy exists.
- *Prompt-enforced autonomy* is unsafe — "a capability the agent's session lacks cannot be exercised by any instruction"; prompts can be injected or ignored.
- *No autonomy* forfeits the entire value thesis (the AI CPO that executes operational work).

**Chosen Solution.** The Trust Ladder (L0 observe → L1 draft → L2 act-with-approval → L3 act-and-notify → L4 autonomous). Promotion is earned by measured accuracy and granted by a signed ledger decision; demotion on bar breach is automatic and logged. Year-1 enforces L0–L2 via approval endpoints and provenance flags; the full capability-token TTL/two-person-rule machinery lands with Trust Ladder GA (Year-2). Edit-distance on accepted artifacts is logged as a Trust-Ladder gate.

**Benefits.** Autonomy is earned, visible, graduated, and reversible; buyers can adopt at L1–L2 with zero autonomy risk; the human is always elevated and credited.

**Tradeoffs.** Year-1 value is capped at draft+approve (no hands-free execution yet); approval endpoints add human latency by design.

**Scalability Impact.** Approval gates are cheap; the autonomy ladder scales by task-class as measured acceptance accrues — autonomy is *upside*, not a dependency, so the system scales adoption without requiring trust upfront.

**Security Impact.** Authority is enforced in the runtime; an L1 agent has no write capability to misuse; every agent write is attributable via `source_run_id`. This is the structural defense that makes prompt injection unable to drive an unauthorized action.

**Cost Impact.** Monetizes at L1–L2 immediately; defers the cost of full token machinery to Year-2; avoids the catastrophic cost of an autonomous mistake on an unearned task.

**Product Impact.** Directly addresses "Enterprises won't grant autonomy" — the Trust Ladder makes autonomy a monetizable upgrade path rather than an adoption blocker, and keeps the "elevate the human" promise.

---

## 21. Confidence Scoring (Calibrated Uncertainty)

**Decision.** Every claim carries a `confidence`; uncitable claims are marked `inference` and rendered differently; quantitative claims must come from the governed metric store; the system measures and reports calibration and abstention honestly.

**Problem Being Solved.** A decision system that sounds equally confident about everything is dangerous. Outcome attribution to a CFO and artifact trust to an engineer both require honest, calibrated uncertainty — and honest abstention when an answer cannot be grounded.

**Alternatives Considered.**
- No confidence signal (treat all output as equally reliable).
- A coarse binary "high/low confidence" flag.
- Confidence as raw model token probabilities only.

**Why Alternatives Were Rejected.**
- *No signal* hides risk and destroys credibility the first time a confident answer is wrong.
- *Binary flags* are too coarse for the Provenance Underline's evidence-weight encoding and for calibration tracking.
- *Raw token probabilities* are poorly calibrated and don't reflect evidence strength or governed-number provenance.

**Chosen Solution.** Confidence is part of the `Claim[]` protocol and is reinforced by evidence weight (single/corroborated/inference/simulated). Numbers are tools, not text. Calibration records are an owned learning loop; the honesty metric targets ≥95% correct abstention on unanswerable sets; outcome reports carry honest uncertainty bands and report misses by policy ("narrative honesty").

**Benefits.** Honest, legible uncertainty; calibration that improves per tenant; abstention instead of confident hallucination; CFO-defensible outcome bands.

**Tradeoffs.** Requires measurement infrastructure (calibration tracking, abstention eval sets) and discipline to surface "unmeasurable as predicted" rather than fabricate.

**Scalability Impact.** Calibration is a per-tenant owned loop that compounds with usage; it improves automatically as the decision-outcome corpus grows.

**Security Impact.** Abstention prevents leaking guesses about data the asker can't see; "n sources withheld" is the honest confidence-aware response.

**Cost Impact.** Avoids the expensive trust failures of overconfident output; routing low-confidence tasks for escalation is cheaper than shipping wrong answers.

**Product Impact.** Underpins the Counterfactual Simulator (always rendered as hypothesis/Violet), Outcome Attribution credibility, and the overall "honest degradation, never silent staleness" promise.

---

# Part II — Architecture Principles

These are the invariants every decision above serves. They are the first thing to state when defending PMOS, because each specific choice is downstream of one of them.

1. **Decisions are the product; documents are exhaust.** The first-class object is the Decision, not the artifact. Generation is a commodity; the graph + ledger + outcome loop is the moat.
2. **The graph is the system; retrieval is a view of it.** Conceptually a Product Knowledge Graph; physically a relational system of record with derived indexes (Postgres + Qdrant + projections).
3. **Every generated sentence is a contract.** No uncited prose. Provenance is material and resolvable in <400ms. (`Claim[]`.)
4. **Autonomy is enforced in the runtime, never in the prompt.** A capability an agent's session lacks cannot be exercised by any instruction. (Capability tokens / approval gates.)
5. **The model layer is rented; the learning loops are owned.** Frontier models are swappable behind the gateway; calibration, per-tenant reranker, and anti-pattern memory compound.
6. **Ingested content is hostile until proven otherwise.** All source content is data, never instructions. (Injection defense-in-depth.)
7. **Elevate the human, visibly.** Attribution, calibration, and outcomes favor the human by default. Adoption depends on it.
8. **Honest degradation, never silent staleness.** Every read surface carries freshness; on SLO breach the UI renders the staleness; model failover shows a visible quality badge.
9. **Speed everywhere; ceremony only where it matters** (commit, grant authority, ship).
10. **Append-only for anything auditable.** Ledger, autonomy log, billing meters: hash-chained, `UPDATE`/`DELETE` revoked at the role level.
11. **Three independent enforcement layers must all fail for a leak.** Isolation is never a single control.
12. **Numbers are tools, not text.** Every quantitative claim originates from a governed metric-store call.
13. **Forward-compatible phasing.** Every Year-1 choice is a named seam toward the end-state (cells, Kafka, Temporal, generic graph), never a dead end.

---

# Part III — Technical Risks

| Risk | Nature | Mitigation (as designed) |
|---|---|---|
| **Cross-tenant leak (existential).** | A single isolation failure exposes a competitor's decision data. | Three independent enforcement layers (RLS + force-injected Qdrant filter + app checks); nightly cross-tenant probes (any hit = sev-0); per-tenant collections/cells at scale. |
| **Prompt injection drives an unauthorized action.** | The AI-native attack: hostile ingested content tries to make an agent act. | Ingestion screening (`quarantine:injection_suspect`) → structural prompt separation (typed evidence blocks only) → tool schemas reject evidence-sourced arguments for sensitive params → capability confinement → CI red-team with tool-call attack-success-rate target 0. |
| **Generated-artifact quality below the engineering bar (P4 veto).** | "AI slop" kills credibility and adoption. | Evidence-native generation + contrarian probe + `Claim[]` + edit-distance as a Trust-Ladder gate; kill/pivot trigger if >30% edit-distance after two quarters of tuning. |
| **Frontier labs subsume the category.** | A model vendor ships "PMOS-lite." | Moat is the owned loops (per-customer graph + decision-outcome corpus + governance + integrations), not the model; re-test every model release. |
| **Architecture divergence (vision vs. build) causes rework.** | The end-state and Year-1 build drift apart. | Phased decisions with explicit seams (outbox transport, orchestration, tenancy, graph layer); every Year-1 choice is forward-compatible by design. |
| **Outcome-attribution credibility with a CFO.** | Predicted-vs-realized claims aren't believed. | Inspectable join logic; honest uncertainty bands; misses reported by policy; a defensible engineering floor (pre-registered prediction + measurement window + holdout/diff-in-diff where feasible). |
| **Model-provider outage or price shock.** | A rented dependency fails or gets expensive. | Model Gateway failover with visible quality badge; tiered routing; provider-swappable behind one surface; a cheaper model improves margins overnight. |
| **Critical-component availability (BFF, Gateway, Postgres).** | Central choke points are single points of failure. | Stateless horizontally-scalable BFF/services; in-cell multi-AZ HA; RTO 1h / RPO ≤5 min; per-tenant/agent/tool/level kill switches; projections rebuildable from the event archive in minutes. |
| **Eventual-consistency confusion.** | Projection lag looks like a bug or stale truth. | Freshness on every read surface; SLO-breach staleness rendered honestly; outbox guarantees no lost events. |

---

# Part IV — Future Evolution Strategy

The guiding sequence is **Earn memory → Earn trust → Earn autonomy → Become infrastructure**, and every Year-1 decision is chosen so the next phase is a *swap or layer*, not a rewrite. The settled triggers (from the Constitution's Open Questions and Technical Decisions) are:

- **Tenancy → Cells (TD-1).** Stay on shared-schema RLS through Year-1; instrument per-tenant load. Move to cells in Year-2 for enterprise residency/isolation; migration is event-replay + router-flip because `organization_id` is already on every row and the cell stamp already runs staging.
- **Events → Kafka/MSK (TD-2).** Keep Redis Streams behind the outbox invariant; swap transport at cell scale. Non-breaking because consumers depend on the event contract, not the broker.
- **Orchestration → Temporal (TD-4, Q7).** Adopt Temporal the quarter an agent run routinely spans **≥3 external mutations requiring rollback**, *or* arbitrary-checkpoint replay becomes contractual for audit disputes — expected to coincide with L3/L4 autonomy GA (Year-2). Runs are kept stateless/checkpointed so the swap is mechanical.
- **Knowledge model → generic bitemporal typed graph (TD-6, Q6).** Stay on the relational hierarchy; instrument the ratio of polymorphic-link rows to strict-FK rows per tenant. Build the typed edge/provenance graph **layered over** the relational tables (do not migrate them) when that ratio crosses **~1:4** on the three largest tenants — expected Year-2.
- **Lexical retrieval → OpenSearch (TD-8, Q8).** Qdrant native hybrid only; stand up a BM25 golden set per large tenant now. Add OpenSearch only when exact-match recall against that golden set drops below **~0.95** on the largest tenant and the reranker can't recover it — likely never for mid-market, possibly Year-3 for whales.
- **Autonomy → full capability tokens / L3–L4 (TD-3).** Approval endpoints + `ai_generated` flags now; full token TTL + two-person-rule machinery at Trust Ladder GA (Year-2), promoted per task-class by measured accuracy.
- **Embeddings (TD-5).** `text-embedding-3-large` @ 3072 dims now; schema is `embedding_model`-parameterized and collections are dimension-fixed, so a model change is a managed blue/green swap.
- **Product surface.** Year-2 brings the Overnight PM, Contradiction Engine, Outcome Attribution, Commitment Ledger, enterprise hardening (SSO/SCIM, residency, ISO 27001), BYOK. Year-3 brings the PMOS Platform & API (third-party governed read/write, agent-to-agent interop), L4 GA for earned task classes, portfolio/M&A modes, and opt-in differential-privacy cross-tenant priors (statistics only, never content).

The strategic thesis: **the model is rented and improves for free; the owned learning loops compound per tenant.** A competitor adopting the same model the day it ships is still years behind on a tenured tenant's graph, outcome corpus, and calibration.

---

# Part V — Interview Talking Points

Crisp, defensible one-liners for an architecture interview or a buyer's technical due-diligence call.

- **"Why relational, not a graph database?"** The graph is the *conceptual* model; the relational schema is the *system of record* with provenance/link tables projecting the graph over it. We get OLTP maturity, RLS isolation, and the Product→Feature→Epic→Story terms PMs and agents actually reason in — and we layer the typed graph on top in Year-2 only when a tenant's topology demands it (TD-6).
- **"How do you stop the AI from hallucinating?"** Every sentence is a `Claim[]` with sentence-level citations, grounded in ACL-trimmed retrieved evidence, then groundedness-verified. Numbers are tools, not text — they come from a governed metric store. Uncitable claims are marked `inference` and rendered differently. The system abstains rather than guess.
- **"How do you stop prompt injection from making an agent act?"** Authority is enforced in the runtime, never in the prompt. An L1 agent has no write capability token, so no instruction — injected or otherwise — can make it write. Plus defense-in-depth: ingestion screening, structural prompt separation, tool-schema rejection of evidence-sourced sensitive args, and CI red-teaming with an attack-success-rate target of zero.
- **"How is multi-tenancy safe on a shared schema?"** Three independent enforcement layers must all fail for a leak: Postgres RLS with `FORCE ROW LEVEL SECURITY`, a force-injected `organization_id` Qdrant filter, and app-layer checks — plus nightly cross-tenant probes where any hit is a sev-0.
- **"What's your moat if the model is rented?"** Exactly that it's rented. The model improves for free; the owned loops — per-tenant graph, decision-outcome corpus, calibration, per-tenant reranker, anti-pattern memory — compound. A model upgrade improves PMOS overnight with zero migration; a competitor on the same model is years behind on a tenured tenant.
- **"Why three protocols instead of one?"** One decision rule: mutates → REST command (idempotent, audited, individually rate-limited); composes ≥3 resources for a screen → GraphQL lens (read-only, complexity-budgeted); server-push → SSE (resumable, stateless). Each property matters and a single protocol would obscure at least one.
- **"Why cap autonomy?"** Autonomy is earned by measured accuracy, never defaulted. The Trust Ladder monetizes at L1–L2 immediately and makes autonomy an upgrade, not a dependency — which is exactly what makes enterprises willing to start.
- **"How do you scale unlimited free viewers?"** Viewers (≈92% of users) hit precomputed projections served from cache; marginal cost ≈ a cache hit. It's architecturally cheap on purpose — ubiquity is the expansion engine.
- **"What if you're wrong about a Year-1 choice?"** Every Year-1 choice is a named seam, not a dead end — Redis Streams→Kafka, BullMQ→Temporal, RLS→cells, relational→typed graph — each with a measurable trigger. We don't migrate on philosophy; we migrate when an instrumented signal crosses a threshold.

---

# Part VI — Common Architecture Questions & Answers

**Q: Isn't shared-schema multi-tenancy a liability for enterprise deals?**
A: It's the Year-1 implementation, not the ceiling. The hard org boundary is enforced by Postgres RLS, backed by a force-injected vector filter and app checks, and probed nightly. For enterprises that require physical isolation or residency, the cell architecture (dedicated cells / customer-VPC, BYOK) is the named Year-2 target — and because `organization_id` is on every row and cells already run staging, getting there is event-replay + router-flip, not a rewrite.

**Q: Why not just use pgvector and avoid a second datastore?**
A: We considered it and rejected it for three concrete reasons: heavily-filtered retrieval performance, the need for native dense+sparse hybrid ranking, and OLTP contention on the system of record. Vectors are *derived* and fully rebuildable from Postgres, so Qdrant adds capability without becoming a second source of truth.

**Q: How do you guarantee an event is never lost or duplicated?**
A: The transactional outbox. The event row is written in the *same* Postgres transaction as the state change, so they commit or fail together. A relay transports outbox rows to the broker. This eliminates the dual-write failure window by construction, and makes the broker swappable.

**Q: What happens when a model provider has an outage?**
A: The Model Gateway fails over to a lower tier and Ask renders a *visible quality badge* — honest degradation, never a silent downgrade. Because everything goes through one gateway, failover and routing are a single, governed concern.

**Q: How can a CFO trust your outcome numbers?**
A: We set a defensible engineering floor: a pre-registered prediction, a defined measurement window, and a holdout or difference-in-differences where feasible, with honest uncertainty bands and misses reported by policy. That's below full causal inference but well above "trust us," and the join logic is inspectable.

**Q: Why eleven agents instead of one capable model?**
A: Per-task authority, per-task evals, per-task model routing, contained blast radius, and clean replay/audit. A monolith couldn't distinguish a draft capability from a write capability, couldn't be evaluated per task type, and would concentrate risk. Agents are stateless, so every run is replayable and auditable.

**Q: Why no WebSockets?**
A: Every Year-1 real-time need is unidirectional server→client (token streams, run progress, the Tide), which SSE serves with less operational surface, native HTTP/2 multiplexing, and trivial resume — keeping the BFF stateless and horizontally scalable. WebSockets are reserved solely for a possible Year-3 multiplayer canvas presence layer, which would never carry authoritative writes.

**Q: What stops a developer's mistake from leaking data?**
A: RLS enforces isolation at the database below the application. The data layer refuses any query without a TenantContext. Background workers that bypass RLS are code-review-required to filter explicitly. And the nightly cross-tenant probe turns any latent leak into a sev-0 incident before a customer finds it.

**Q: How do you avoid vendor lock-in on identity and models?**
A: Identity lives in Clerk but *authority lives in PMOS policy data*, so Clerk is a swappable identity source. Models live behind the Model Gateway with hot-swappable routing, so a provider is swappable without touching agents. We rent capability; we own the governance.

**Q: When does the architecture actually change, concretely?**
A: Only on instrumented triggers, never on opinion: cells when a tenant needs isolation/residency or whale load demands it; Kafka at cell scale; Temporal when runs span ≥3 rollback-requiring mutations or audit-replay becomes contractual; the typed graph when polymorphic-link rows hit ~1:4 of strict-FK rows on the largest tenants; OpenSearch when exact-match recall drops below ~0.95 and the reranker can't recover it. Each trigger is measured from day one.

**Q: What is the single most important invariant to protect?**
A: Tenant isolation — it's the existential threat. Everything else (a stale projection, a degraded model tier, a slow agent) is recoverable and rendered honestly; a cross-tenant leak is not. That's why isolation is three independent layers plus a nightly probe, not a single control.

---

*This document is itself a decision record. Any amendment is recorded with author, rationale, and date — consistent with PMOS's self-hosting principle that amendments to the constitution are themselves decisions.*
