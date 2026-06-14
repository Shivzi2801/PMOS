# PMOS — Architecture Review

### A Four-Panel Principal Review · Readiness Assessment for Implementation

**Review panel (composite voices):** Principal Architect (Stripe) · Staff Engineer (OpenAI) · Distinguished Engineer (Amazon) · CTO (Series-C SaaS)
**Documents reviewed:** `PMOS_MASTER_SPEC_Final` · `Feature_Inventory` · `User_Flows` · `API_Design` · `API_Inventory` · `Frontend_Modules` · `Backend_Modules` · `PMOS_ENGINEERING_DECISIONS`
**Verdict in one line:** This is a top-decile *design* corpus — internally consistent, phasing-aware, and unusually disciplined about seams. It is **not yet implementation-ready** because several load-bearing claims are asserted as targets without the runtime mechanics, numeric thresholds, and failure-path specs needed to build and operate them. The gap is *operational depth*, not *architectural soundness*.

A note on calibration before the findings: most of what follows is **Medium or below**. That is the honest read — the documents already close the majority of what a review like this usually catches (tenancy invariant, outbox, capability tokens, ACL inheritance, honest degradation, forward-compatibility seams). The review is therefore biased toward the residual: the places where a confident sentence in the spec would become a two-week surprise in the sprint.

---

## How to read severity

- **Critical** — blocks a safe production launch; a customer or auditor would hit it.
- **High** — must be resolved before GA; will cause rework or incident if deferred.
- **Medium** — should be closed during the build; cheap now, expensive later.
- **Low** — polish, or a documentation/traceability gap rather than a design gap.

---

# 1. Contradictions

**1.1 — "Hash-chained ledger" described as both Year-1 and Year-2.** *Severity: Medium.*
The Master Spec §9 worked trace says the commit ceremony "writes the hash-chained ledger entry," and §16/Backend M-21/M-28 treat the hash chain as Year-1 machinery. But TD-6 explicitly defers "the full generic bitemporal graph **+ hash-chained Decision Ledger**" to Year-2. **Impact:** an engineer cannot tell whether the Year-1 Decision Ledger is hash-chained or merely append-only-with-audit. These are different schemas and different audit guarantees. **Recommendation:** state it once, unambiguously. The defensible resolution: the *audit fabric* hash-chain (M-21) ships Year-1 and the Decision Ledger uses it from day one; what defers to Year-2 is the *generic typed-graph projection* over the ledger, not the chaining. Edit TD-6 to separate "graph projection (Y2)" from "hash-chained ledger (Y1)."

**1.2 — `auth_provider` enum: retained vs. removed.** *Severity: Low.*
TD-7 says the DB `auth_provider` enum is "retained only as an identity-source label," while the narrative elsewhere says "passwords never touch PMOS." Not a true contradiction, but the `password` *value* of that enum still exists in the schema as written. **Impact:** a careless migration could resurrect a credential path. **Recommendation:** drop the `password` enum value entirely; keep only IdP-source labels (`saml`, `oidc`, `scim`). A nonexistent value cannot be misused.

**1.3 — "No COUNT(*) per page" vs. surfaces that imply totals.** *Severity: Low.*
Cursor-only pagination forbids `COUNT(*)`, yet Feedback Intelligence ("clustered, quantified, tied to revenue") and the Arena imply counts and totals. **Impact:** product expectations (a count badge, "1,240 signals") collide with the pagination contract. **Recommendation:** specify that aggregate counts come from the **governed metric store** (precomputed rollups), never from paginating reads — consistent with "numbers are tools." Make this explicit in API_Design §2.4.

**1.4 — Workspace as "soft" boundary vs. lens-sharing guarantees.** *Severity: Medium.*
The org boundary is hard RLS; the workspace/Stream boundary is "soft" app-layer ABAC, justified as defense-in-depth (join-based RLS degrades plans). Separately, lens-sharing "never widens what a recipient may see." **Impact:** a soft, app-enforced boundary is exactly where a missing predicate leaks *within* a tenant — and intra-tenant over-exposure (a PM seeing another team's unreleased strategy) is a real enterprise objection, not just a competitor-leak concern. **Recommendation:** treat the workspace/Stream boundary as a first-class, separately-tested control with its own probe (an intra-tenant analogue of the cross-tenant probe), not merely "app-layer." Document the test.

> **Net on contradictions:** there are no architecture-breaking contradictions. The four above are *specification ambiguities* that will each cost a sprint of rework if an engineer guesses wrong. They are cheap to close now.

---

# 2. Missing Features / Specification Gaps

**2.1 — Disaster recovery for Qdrant and Redis is unspecified.** *Severity: High.*
RTO 1h / RPO ≤5 min is stated for the cell, and "vectors are rebuildable from Postgres" is the stated recovery story. But rebuilding `10⁸` chunks of embeddings from Postgres is hours-to-days and costs real embedding spend — it cannot meet a 1h RTO. Redis carries Streams (in-flight events) and BullMQ (in-flight work); its persistence mode (AOF/RDB), replication, and failover are unspecified. **Impact:** a Qdrant node loss or Redis failover is an unrehearsed incident that violates the stated RTO. **Recommendation:** specify Qdrant snapshotting + multi-node replication (not "rebuild from Postgres" as the primary path); specify Redis AOF + replica + Sentinel/cluster; define RPO/RTO *per datastore*, not just for the cell as a whole.

**2.2 — Postgres high-end scaling mechanics are asserted, not designed.** *Severity: High.*
`organization_id` is called a "ready-made shard key," but there is no statement of *when* or *how* Postgres partitions/shards before cells arrive, nor how the largest tables (`document_chunks`, `agent_run_steps`, audit tables, outbox) are partitioned. At `10⁷` records / `10⁸` chunks per large tenant and 50M ingestion events/day, the outbox and step-trace tables become the hottest write paths in the system. **Impact:** table bloat, vacuum pressure, and outbox-relay lag well before cells. **Recommendation:** specify declarative partitioning (by `organization_id` and/or time) for the high-write tables now; define the outbox retention/archival policy and the `agent_run_steps` partitioning + TTL; state the connection-pool topology (PgBouncer pools per service, sizing).

**2.3 — No idle/zombie-run reaping or agent-run timeout policy.** *Severity: Medium.*
Agent runs are stateless, checkpointed, and cancelable, and there is a `job-reaper-worker` for stale jobs. But there is no stated per-run wall-clock budget, no max-step ceiling, and no runaway-cost circuit-breaker on a single run. **Impact:** a looping or stuck agent burns tokens against a tenant's meter until a human notices. **Recommendation:** define a per-task-class max steps, max tokens, and wall-clock budget enforced at the orchestrator; auto-fail + checkpoint on breach; surface as honest degradation.

**2.4 — Per-tenant and per-run cost ceilings / spend caps are absent.** *Severity: High (commercial).*
Metering is excellent (append-only, idempotent, hash-chained). But there is no *spend cap* — nothing that stops a tenant (or a bug, or an abusive prompt) from running up unbounded model cost in real time. Consumption pricing without a cap is a CFO's nightmare on both sides of the table. **Impact:** a single runaway tenant can blow platform COGS for a billing period; a customer can receive a surprise invoice and churn. **Recommendation:** add real-time budget enforcement at the Model Gateway (per-tenant soft alert + hard cap, per-run cap), with honest-degradation behavior at the cap (queue to batch lane, or block with a clear message), not just post-hoc metering.

**2.5 — Eval-harness thresholds are named but not numeric.** *Severity: Medium.*
The eval harness "gates releases" and the CI red-team targets "attack-success-rate 0," and edit-distance has a 30% kill/pivot trigger. But the *release gate itself* has no published pass/fail bands per (agent, task-type): what groundedness score, what calibration error, what abstention accuracy blocks a deploy? **Impact:** "the eval harness gates releases" is unfalsifiable without thresholds; teams will ship to vibes. **Recommendation:** publish the gate as a table of numeric thresholds per agent/task-class, versioned alongside the agent. Attack-success-rate 0 is correct as an absolute; the quality gates need numbers.

**2.6 — Connector failure semantics under-specified.** *Severity: Medium.*
Connectors promise ≤2-min freshness and ≤1h ACL reconciliation with webhooks/CDC. There is no spec for sustained source outage (webhook gap > N min), backfill-after-outage ordering vs. the live lane, or how stale-ACL windows are surfaced. **Impact:** during a Zendesk/Jira outage, freshness silently lapses or ACLs drift past the 1h SLO without a visible badge. **Recommendation:** define per-connector health states, the freshness-badge behavior on source outage, and the catch-up reconciliation that must not let a stale ACL widen access.

**2.7 — Living Sync is one-way; bidirectional drift is a named V1 feature but Year-1 ships push-only.** *Severity: Low (expectation).*
Core Feature #4 promises "bidirectional, diff-and-rationale sync"; Backend M-31 ships **one-way** (spec→Jira). **Impact:** a buyer sold on bidirectional coherence gets push-only in Year-1. This is correctly phased but easy to over-promise in GTM. **Recommendation:** label the wedge explicitly as one-way in customer-facing material; keep "drift detection" (read-back) distinct from "drift correction" (write-back, Year-2).

**2.8 — No specified approach for embedding-model migration cost/coverage during blue/green.** *Severity: Low.*
Blue/green collection swap on model change is specified, but not the *interim* state: during a 3072-dim model change across `10⁸` chunks, retrieval quality and cost during the dual-write window are undefined. **Recommendation:** specify dual-read/shadow-eval during the swap and a per-tenant cutover, not a global flip.

---

# 3. Security Risks

The security model is the strongest part of the corpus. Threats are ranked, defense-in-depth is real (three independent isolation layers; four-layer injection defense), and capability confinement is enforced at the tool service, not the prompt. The residual risks are about *proof* and *intra-tenant* exposure.

**3.1 — "Attack-success-rate 0" is a target without a measurement corpus spec.** *Severity: High.*
Targeting 0 successful tool-call injections is correct. But there's no spec of the *red-team corpus*: how many attack classes, who maintains it, how it grows with each new tool, and what "0 over the corpus" means statistically (0 in a 10-case suite is meaningless). **Impact:** the headline guarantee is unaudited. **Recommendation:** specify the injection corpus as a living, versioned asset with a minimum case count per tool, mandatory expansion on every new tool/parameter, and a documented methodology a security reviewer can inspect.

**3.2 — Intra-tenant over-exposure (the "soft" boundary).** *Severity: High.*
See 1.4. The org boundary is hard; the within-tenant boundary is app-layer. Enterprises increasingly demand that a contractor PM or a soon-to-depart employee *cannot* read another stream's unreleased strategy. A soft boundary is one bug away from that. **Impact:** a within-tenant leak is a real enterprise sale-killer even though it is not the "existential" cross-tenant case. **Recommendation:** add an intra-tenant access probe, ABAC test coverage as a release gate, and consider RLS on `workspace_id` for the highest-sensitivity tables despite the plan-degradation cost (measure it; it may be acceptable on the small set of sensitive tables).

**3.3 — Stream-ticket and JWT replay/rotation details thin.** *Severity: Medium.*
Single-use 60s stream tickets and JWKS-verified JWTs are good. Not specified: JWT max lifetime + rotation, revocation on SCIM deprovisioning latency (a deprovisioned user with a live JWT), and stream-ticket binding to the SSE connection origin. **Impact:** a deprovisioned editor retains access until token expiry; a leaked ticket within 60s is replayable if not connection-bound. **Recommendation:** specify short JWT TTL + refresh, immediate session revocation on SCIM deprovision (server-side denylist keyed on user/session), and bind the stream ticket to a single connection.

**3.4 — Secrets management for connector credentials unspecified.** *Severity: Medium.*
Connectors hold OAuth tokens / API keys for Zendesk, Jira, Gong, Salesforce — high-value source credentials (threat #3 names credential theft). Where they're stored, how they're encrypted at rest, rotation, and KMS envelope are not specified for Year-1 (BYOK is Year-2). **Impact:** a breach of the connector store is a breach of every connected source. **Recommendation:** specify per-tenant KMS-enveloped secret storage with rotation now; BYOK in Year-2 extends it, not replaces it.

**3.5 — PII screening efficacy and DLP on egress to model providers.** *Severity: Medium.*
Ingestion does PII screening and ZDR contracts govern providers. Not specified: what happens when PII *legitimately* must go to a model (it's in the source content), how the ZDR guarantee is verified rather than contracted, and whether there's egress DLP. **Impact:** "no cross-tenant training by default" and ZDR are contractual claims; an auditor will ask for technical enforcement. **Recommendation:** document the technical ZDR enforcement (provider config, no-retention headers/endpoints) and a sampling-based egress audit.

---

# 4. Scalability Risks

The read-scaling story (projections, cache-hit viewers, tiered retrieval, int8 quantization) is genuinely strong and is the right answer for the 92%-viewer workload. The risks are on the write and stateful-store side.

**4.1 — Redis as Streams + queue + cache is a triple single-point-of-stress.** *Severity: High.*
One Redis serving cache, event transport, and BullMQ couples three failure domains. Under 50M ingestion events/day, Streams throughput and BullMQ contention can degrade the cache that the interactive read budgets depend on. **Impact:** an ingestion spike degrades interactive latency (Peek <100ms, Sheet <150ms) — the opposite of the decoupling the design intends. **Recommendation:** separate Redis instances per role (cache vs. streams vs. queue) even in Year-1, or move queues/streams to dedicated instances; the Kafka seam (TD-2) helps streams later but the cache/queue split should happen now.

**4.2 — Single-collection-per-granularity Qdrant: whale-tenant blast radius.** *Severity: Medium.*
Payload-partitioned single collection is efficient but means one whale tenant's `10⁸` chunks share a collection with hundreds of small tenants until cells. **Impact:** index maintenance, segment merges, and a corruption event have a multi-tenant blast radius before the Year-2 per-tenant-collection move. **Recommendation:** bring the per-large-tenant dedicated collection forward as an escape hatch (the spec already names dedicated cells for whales; do the same for Qdrant collections independently of full cells).

**4.3 — Outbox relay is a throughput chokepoint.** *Severity: Medium.*
The single relay-worker draining outbox→Streams is the funnel for every state change. At 50M events/day plus 3M agent tasks/day, relay lag directly drives the freshness badge (i.e., visible staleness). **Impact:** under load, "honest degradation" becomes "constant degradation." **Recommendation:** specify relay horizontal scaling (partitioned outbox consumption by `organization_id` hash), target relay-lag SLO, and the alert threshold — not just the freshness badge as the safety valve.

**4.4 — GraphQL complexity budget vs. the Brief/portfolio lenses.** *Severity: Low.*
Cost ≤1000 / depth ≤8 is sensible, but the Brief and portfolio lenses compose many resources; persisted queries help, but a heavy lens may legitimately exceed budget and force splitting into multiple round-trips, hurting the <1.5s cold-load budget. **Recommendation:** validate the heaviest real lenses against the budget early; precompute the Brief as a projection (the design implies this) rather than resolving it live in GraphQL.

---

# 5. AI Architecture Risks

The `Claim[]` protocol, governed-numbers rule, contrarian probe, and runtime-not-prompt authority are excellent and are the right structural answers. Residual risk is in *quality measurement* and *cost/latency under real model behavior*.

**5.1 — Groundedness verification is a step, not a specified mechanism.** *Severity: High.*
"Claim-grounded generation → groundedness verification" is named everywhere but the *verifier* is undefined: is it an LLM judge (cost, latency, its own hallucination), a retrieval-overlap check, or NLI? The honesty metric (≥95% correct abstention) depends entirely on this. **Impact:** the central trust claim ("every sentence is a contract") rests on an unspecified component. **Recommendation:** specify the verifier mechanism, its own eval, its latency budget (it sits in the Ask path under the <700ms first-token budget), and its cost — an LLM-judge on every claim is expensive at scale.

**5.2 — Ask first-token <700ms with retrieval + ACL-trim + grounding in the path.** *Severity: Medium.*
The <700ms first-token budget must absorb: parallel retrieval legs, fusion, rerank (cross-encoder), pre-fusion ACL trim, then generation start. That is a lot before the first token. **Impact:** the budget may be unachievable on cold cache or large candidate sets, making the flagship interaction feel slow. **Recommendation:** publish a latency budget *decomposition* across the retrieval stages and prove it on the largest tenant; consider speculative first-token streaming while verification runs.

**5.3 — Per-tenant reranker heads: training data, cold-start, and drift.** *Severity: Medium.*
Per-tenant reranker heads are a real moat but introduce per-tenant ML lifecycle: cold-start (new tenant has no signal), training cadence, drift monitoring, and rollback. None is specified. **Impact:** an under-trained or drifted head degrades retrieval for that tenant silently. **Recommendation:** specify the base-head fallback for cold-start (already implied), the retrain trigger, and per-tenant retrieval-quality monitoring with auto-revert to base.

**5.4 — Model-swap regression risk vs. "improves overnight with zero migration."** *Severity: Medium.*
The gateway makes swaps mechanical, but a new model can *regress* on a tenant's tuned prompts/evals even as it improves on average. "Improves overnight" is optimistic. **Impact:** a silent quality regression on swap. **Recommendation:** make model swaps gated by the eval harness per agent/task-class (shadow eval before promotion), not a config flip — i.e., the swap is mechanical but *not unguarded*.

**5.5 — Contrarian probe and groundedness add multiplicative cost.** *Severity: Low.*
Mandatory contrarian probe + groundedness verification + tiered retrieval means several model calls per artifact. **Impact:** per-task COGS is higher than a naive generator; fine for the value prop but must be in the COGS model. **Recommendation:** ensure the metering and COGS-per-task budget explicitly include probe + verifier calls.

---

# 6. Database Risks

**6.1 — `agent_run_steps` and audit tables are unbounded high-write growth.** *Severity: High.*
Step traces for 3M tasks/day and append-only audit/outbox tables grow without a stated retention/partition/archival policy (S3 archive is mentioned for events generally, not a concrete per-table TTL). **Impact:** these become the largest, hottest tables; vacuum and index bloat threaten OLTP latency. **Recommendation:** time-partition + TTL + S3 archival per high-write table, defined now (see 2.2).

**6.2 — UUIDv7 as Qdrant point ID couples key strategy across stores.** *Severity: Low.*
Elegant (no mapping table), but it hard-couples the Postgres PK strategy to Qdrant's point-ID constraints forever. **Impact:** a future vector-engine change (OpenSearch leg, or replacing Qdrant) inherits this coupling. **Recommendation:** acknowledge the coupling explicitly as an ADR; the engine-agnostic `/search` contract already mitigates it at the API layer.

**6.3 — Soft-delete + 30-day trash vs. RLS vs. GDPR 24h erasure interaction.** *Severity: Medium.*
Three deletion concepts coexist: soft-delete (`deleted_at`, 30-day trash), GDPR erasure (≤24h via tombstones/redacted stubs), and the hash-chain (never deleted). The interaction is described but not fully reconciled: does a GDPR erasure within the 30-day trash window correctly redact across *all* derived stores (Qdrant points, caches, projections, provider-side logs)? **Impact:** an incomplete erasure cascade is a regulatory finding. **Recommendation:** specify the erasure cascade's coverage of *every derived store* and provider-side retention, with a verification step (an erasure analogue of the chunk reconciler).

**6.4 — Reconcilers (nightly Postgres↔Qdrant, hourly ACL) are correctness-critical but failure-silent.** *Severity: Medium.*
If the nightly reconciler or the ACL reconciler fails or lags, drift accumulates (stale ACLs = over-exposure; index drift = wrong answers) potentially unnoticed for a day. **Impact:** a silently-failed ACL reconciler is a security exposure, not just a quality bug. **Recommendation:** treat reconciler health as a monitored SLO with paging on miss; ACL-reconciler failure should be sev-1+ (it gates the security guarantee).

---

# 7. UX Risks

The design language ("Calm Authority," the Provenance Underline, system-speaks-first, honest degradation) is distinctive and coherent. Risks are about the gap between an elegant model and real human behavior.

**7.1 — "No folders, no organize" is a strong bet against user habit.** *Severity: Medium.*
The IA deliberately removes folders/page-trees/project lists ("if a user wants to organize, retrieval has failed"). This is a beautiful principle and a real adoption risk: enterprise users expect to *organize*, and retrieval-only navigation must be near-perfect to replace that instinct. **Impact:** if retrieval is even occasionally wrong, users feel lost with no fallback structure. **Recommendation:** keep the principle but ensure Streams (the one human-curated container) are powerful enough to serve as the safety valve; instrument "I can't find X" events as a retrieval-quality signal.

**7.2 — Provenance Underline accessibility and density.** *Severity: Low.*
Evidence weight encoded as underline thickness + glyph (with non-color redundancy — good). At document density, many underlines may create visual noise and screen-reader verbosity. **Recommendation:** validate with real artifacts and AT users; provide a density toggle.

**7.3 — Honest-degradation badges everywhere risk "alarm fatigue."** *Severity: Low.*
Freshness badges, quality-tier badges, abstention notices, simulation (Violet) — honesty is right, but pervasive caveats can erode confidence if over-shown. **Recommendation:** tune thresholds so badges appear on *material* degradation only; test the calm-vs-noisy balance.

**7.4 — Approval-gated L2 workflow latency vs. "5–6 hrs/week saved."** *Severity: Medium.*
Every L2 action requires human approval (correct for trust). But the wedge ROI promise (5–6 hrs/PM/week) depends on approvals being fast and batched, not a new queue of micro-approvals that recreates the status-report tax. **Impact:** approval friction could eat the time savings. **Recommendation:** design batch-approval and trust-graduated auto-approval (the Trust Ladder) into the wedge UX explicitly; measure approval time as a first-class metric.

---

# 8. Cost Risks

**8.1 — No real-time spend cap (see 2.4).** *Severity: High.* Repeated here because it is both a commercial and an operational risk. Metering ≠ capping.

**8.2 — Per-artifact model-call multiplication (see 5.5).** *Severity: Medium.* Contrarian probe + groundedness verifier + tiered cascade + reranker means COGS-per-artifact is several model calls; the 35%-consumption-of-revenue and per-task-COGS-budget targets must be modeled against *measured* multi-call cost, not single-call estimates.

**8.3 — Embedding re-cost on model migration.** *Severity: Medium.* Re-embedding `10⁸` chunks per large tenant on a model change is a large, lumpy cost not visible in steady-state COGS. **Recommendation:** budget embedding-migration as a discrete line item; the `content_hash` dedupe helps within a model but not across a dimension change.

**8.4 — Qdrant + Postgres + Redis + S3/WORM + Supabase + Clerk + two model providers is a wide vendor surface for a Series-C.** *Severity: Medium.* Each is justified, but the operational and contractual cost (and the on-call surface) of seven+ external dependencies is real at this stage. **Recommendation:** confirm each has a credible failover/runbook and that the team can actually operate all of them; consider consolidation where the seam allows (e.g., is Supabase Storage worth a separate vendor vs. S3 with a CDN?).

---

# 9. Operational Risks

**9.1 — Observability is named (OTel trace linkage) but not specified as a system.** *Severity: High.*
OTel is referenced for audit linkage, and there are SLOs and a freshness tracker. But there is no specified observability *stack*: metrics/dashboards per SLO, the alert catalog, log aggregation, distributed-trace sampling strategy, or the on-call runbook set. A system with 42 modules, eight service groups, and seven datastores cannot be operated on trace-linkage alone. **Impact:** the SLOs (Brief by 6am ≥99.5%, interactive 99.9%, all §13 latency budgets) are unmeasurable and unenforceable without this. **Recommendation:** specify the observability platform, the per-SLO dashboard and alert, and a runbook per failure mode (relay lag, reconciler miss, provider failover, kill-switch engage) before GA. This is the single biggest *operational* gap.

**9.2 — Runbooks for the named failure modes are absent.** *Severity: Medium.* Kill switches, failover, and reconcilers are specified as mechanisms; the *operator procedures* (when to engage a per-tenant kill switch, how to recover from a half-drained outbox, how to re-run a failed reconciler safely) are not. **Recommendation:** write runbooks alongside each mechanism; an unrehearsed kill switch is a liability.

**9.3 — Cell creation "≈2h via Terraform" and "stamp ships day one" are claims to prove.** *Severity: Medium.* The migration-not-rewrite story (event-replay + router-flip) is architecturally sound *if* the replay and router actually exist and are tested. They are described as Year-2. **Impact:** the entire "no rewrite" guarantee is untested until Year-2; if replay doesn't work as imagined, the migration becomes a rewrite under pressure. **Recommendation:** build and test event-replay into a fresh cell in Year-1 (even at small scale) to de-risk the central scalability promise before it's needed.

**9.4 — Backfill vs. live-lane interaction under real load.** *Severity: Low.* Three priority lanes are specified; the actual scheduler fairness and starvation guarantees (bulk never starves, but also bulk eventually completes) are not. **Recommendation:** define lane SLOs including a bulk-completion ceiling.

---

# 10. Enterprise Adoption Risks

**10.1 — Year-1 ships shared-schema; many enterprises require isolation/residency on day one.** *Severity: High (GTM).*
The wedge targets mid-market (correct), but the spec's own ACV/NRR targets imply enterprise expansion, and enterprise procurement frequently *requires* dedicated isolation, data residency, and BYOK — all Year-2. **Impact:** the sales motion can outrun the architecture; a deal needs Year-2 capabilities in a Year-1 quarter. **Recommendation:** hold GTM discipline to mid-market until cells land, OR bring a single dedicated-cell capability forward for one lighthouse enterprise (the architecture allows it; the question is timing the spend). Be explicit which.

**10.2 — Compliance posture is SOC 2 Type II in progress; ISO 27001 and BYOK are Year-2.** *Severity: Medium.* Enterprises often gate on completed SOC 2 Type II (12-month observation) and increasingly ISO 27001. **Impact:** procurement delays. **Recommendation:** start the SOC 2 observation window immediately (it's a calendar dependency, not an engineering one); sequence ISO 27001 evidence collection in parallel.

**10.3 — Autonomy buyer education.** *Severity: Low.* The Trust Ladder is the right answer to "enterprises won't grant autonomy," but it requires buyer education to land as "upside, not dependency." **Recommendation:** this is a GTM/enablement task, not architecture — flagged for completeness.

**10.4 — Source-ACL inheritance depends on sources exposing usable ACL data.** *Severity: Medium.* The whole permission-safe-retrieval guarantee assumes connectors get `read_principals` from each source. Some sources expose weak or no ACL data. **Impact:** for those sources, PMOS either over-restricts (useless) or must make an access assumption (risky). **Recommendation:** define the fallback policy per source explicitly (fail-closed by default), and surface coverage honestly ("ACL fidelity: high/medium/low per source"), consistent with the existing coverage-upgrade framing.

---

# Architecture Readiness Score

Scored 1–10, where 10 = production-ready with no material gaps, and the score reflects *implementation readiness*, not design ambition.

| Area | Score | One-line justification |
|---|---:|---|
| **Product** | **9/10** | Exceptional clarity of vision, principles, scope, and non-goals; the loop is coherent and the moat thesis is sound. Minor: a few features (bidirectional sync) risk GTM over-promise. |
| **UX** | **8/10** | Distinctive, principled, accessible-by-design. Held back by unproven behavioral bets (no-folders) and approval-friction risk to the ROI promise. |
| **AI** | **8/10** | Best-in-class structural choices (`Claim[]`, runtime authority, governed numbers, contrarian probe). Held back by the unspecified groundedness verifier and unproven latency/cost budgets. |
| **Frontend** | **8/10** | Clear module decomposition, dev-fixture parallelization, contract-first. Mostly an execution question; few architectural risks. |
| **Backend** | **8/10** | Disciplined module spec, correct invariants (outbox, tenancy, tokens), seams well-placed. Held back by partitioning/scaling mechanics and DR specifics. |
| **Security** | **7/10** | Strong threat model and defense-in-depth; the existential case is well-handled. Held back by intra-tenant boundary softness, unaudited attack-success-rate claim, secrets/rotation gaps. |
| **Scalability** | **6/10** | Read-path is excellent; write-path and stateful-store scaling (Redis coupling, outbox relay, Postgres partitioning, Qdrant blast radius) are asserted more than designed. |
| **Observability** | **4/10** | The single weakest area. SLOs and OTel linkage are named, but no observability *system*, alert catalog, or runbooks. SLOs are currently unenforceable. |
| **Enterprise Readiness** | **6/10** | Identity/audit/compliance direction is right; isolation/residency/BYOK/ISO are Year-2, which constrains the enterprise motion and must be sequenced honestly. |

**Composite (unweighted): ~7.1/10.** Weighting toward the launch-blocking areas (Security, Scalability, Observability, Enterprise) pulls the *operational* readiness lower than the *design* readiness — which is the core finding.

---

# Is PMOS ready for implementation?

**Partially — and more ready than most systems at this stage, but not yet greenlight-to-GA.**

The honest framing: PMOS is **ready to begin implementation of its Foundation wave today**, and it is **not yet ready for a production GA commitment**. Those are different questions, and conflating them is the trap.

**What is ready now (start building):**
- The Foundation critical path (tenancy/RLS, identity, core persistence, outbox, BFF, model gateway, audit fabric) is specified to a build-ready level. An engineer can start M-01 through M-07 and M-21 against these documents with minimal ambiguity.
- The invariants that are expensive to retrofit — `organization_id` on every row, outbox-only eventing, `Claim[]` everywhere, capability tokens, ACL inheritance at upsert — are correct and locked. Getting these right at the start is exactly what this corpus does.
- The seams (TD-1/2/4/6/8) are well-placed; the phasing discipline is real and rare.

**What must be closed before GA (the remaining work):**

1. **Observability as a system** (9.1) — the highest-leverage gap. Specify the metrics/alert/dashboard/runbook layer before any SLO can be claimed. *Blocks GA.*
2. **Datastore DR and Postgres scaling mechanics** (2.1, 2.2, 6.1) — DR for Qdrant/Redis, partitioning + retention for the high-write tables, connection-pool topology. *Blocks GA.*
3. **Real-time spend caps** (2.4, 8.1) — capping, not just metering, on consumption pricing. *Blocks GA for the consumption tier.*
4. **Groundedness verifier specification** (5.1) — the mechanism the central trust claim rests on, with its own eval and latency/cost budget. *Blocks GA of generative surfaces.*
5. **Intra-tenant boundary hardening + the attack-corpus spec** (3.1, 3.2) — make the "soft" boundary a tested control and make "attack-success-rate 0" auditable. *Blocks enterprise GA.*
6. **Numeric eval-gate thresholds** (2.5) and **erasure-cascade coverage** (6.3) — make "the harness gates releases" and "GDPR ≤24h" falsifiable and complete.
7. **De-risk the cell migration in Year-1** (9.3) — prove event-replay into a fresh cell at small scale, so the "no rewrite" promise is tested before it's load-bearing.

**The one-paragraph verdict for the board:**
PMOS's *architecture* is sound and its *design discipline* is top-decile — the hard, irreversible decisions are right, and the phasing protects the future without over-building the present. What remains is not redesign but **operational hardening**: the system can be built, but it cannot yet be *run* to the standard it promises, because observability, datastore resilience, cost control, and a few security-proof artifacts are named rather than specified. Greenlight the Foundation build now; gate the production GA on closing the seven items above. None requires rethinking the architecture; all require turning confident sentences into runbooks, thresholds, and tested failure paths.

---

*Reviewed against the documents as written. Where a finding says "unspecified," it means absent from the reviewed corpus, not necessarily absent from the team's intent — several of these may already live in infrastructure or ops material outside this document set, in which case the recommendation is simply to fold them into the spec so they are traceable and testable.*
