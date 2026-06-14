# PMOS — Operational Readiness

### Resolving Every High & Medium Architecture-Review Finding · The Run Book

**Status:** Operational Readiness Specification v1.0 · **Audience:** platform/infra engineers, SREs, backend & AI engineers, on-call, security engineering
**Source:** resolves the High and Medium findings in `PMOS_ARCHITECTURE_REVIEW.md` against the build defined in `Backend_Modules.md` and the invariants in `PMOS_MASTER_SPEC_Final.md`.
**Rule of precedence:** where this document specifies a runtime mechanism, threshold, or procedure that the architecture corpus left as a named target, *this document is authoritative for how it is built and operated.* Where it would conflict with a Constitution invariant, the invariant wins and this document is wrong and must be fixed.

---

## 0. How to read this document

The architecture review's central finding was that PMOS is *designed* to a top-decile standard but cannot yet be *run* to the standard it promises: observability, datastore resilience, cost control, and several security-proof artifacts were **named rather than specified**. This document closes that gap. It turns confident sentences into thresholds, runbooks, and tested failure paths.

Two framing rules:

1. **Every claim here is falsifiable.** Where the review said "unspecified," this document gives a number, a procedure, or a test. A target without a measurement is treated as not done.
2. **Honest degradation over silent failure, everywhere.** Consistent with Principle 8, every mechanism below has a defined *degraded* state that is surfaced (a badge, an alert, a blocked action with a clear message) rather than a silent lapse.

Each of the sixteen sections follows the same template: **Issue · Risk · Solution · Implementation · Operational Procedures · Monitoring · Alerting · Recovery.** The cross-cutting SLO/SLI/error-budget/RTO-RPO/alert-threshold tables are consolidated in Part III so they can be lifted directly into a monitoring config.

Finding-to-section traceability (review § → this doc §): 2.1→§1,§2; 2.2/6.1→§3,§4; 2.3→§5,§6; 2.4/8.1→§7,§8; 2.5→§9; 5.1/review-AI→§9,§10; 2.6→§11; 4.3→§4,§12; 3.2/1.4→§13; 3.3→§14; 9.1/9.2→§15; 9.x→§16. The other Medium findings (1.1, 1.3, 6.3, 6.4, 5.3, 5.4, 7.4, 8.x, 10.x) are resolved inline within the most relevant section and tagged.

---

# Part I — Datastore Resilience

---

## 1. Qdrant Backup and Recovery Strategy

**Issue.** The review (2.1) found that "vectors are rebuildable from Postgres" was the *only* stated recovery path. Re-embedding `10⁸` chunks per large tenant is an hours-to-days operation and incurs real embedding spend — it cannot satisfy the stated 1h RTO. Qdrant had no snapshot, replication, or restore procedure.

**Risk.** A Qdrant node or volume loss becomes an unrehearsed multi-hour outage of *all* generative and search surfaces (Ask, Feedback Intelligence, the Brief's evidence). Because a single payload-partitioned collection is shared across many tenants (review 4.2), the blast radius is multi-tenant. Rebuild-from-Postgres as the primary path also re-bills the customer's (or platform's) embedding budget for a recovery event.

**Solution.** Treat Qdrant as a recoverable store in its own right, with three independent recovery tiers, and demote "rebuild from Postgres" to the tier-3 backstop it should always have been:
- **Tier 1 — replication (RTO seconds–minutes):** run Qdrant as a multi-node cluster with replication factor ≥2 per shard across AZs. A single node loss is transparent.
- **Tier 2 — snapshots (RTO ~15–30 min):** scheduled collection snapshots to S3, every 6h plus before any blue/green collection swap. Restore is a collection import, not a re-embed.
- **Tier 3 — deterministic rebuild from Postgres (RTO hours, last resort):** the existing `index-reconciler` path; used only when both replicas and the latest snapshot are unrecoverable, or to heal partial drift.

This resolves review 4.2 by also bringing the **per-large-tenant dedicated collection** escape hatch forward: any tenant exceeding 2×10⁷ chunks gets its own collection, capping snapshot/restore blast radius for whales independently of the Year-2 cell move.

**Implementation.**
- Qdrant cluster: minimum 3 nodes, replication factor 2, shards sized so any one shard ≤ ~10⁷ points. Anti-affinity across AZs.
- Snapshot worker (`qdrant-snapshot-worker`, cron 6h): triggers Qdrant snapshot API → ships to `s3://pmos-qdrant-snapshots/{cell}/{collection}/{ts}`; WORM-lite lifecycle (retain 14 daily + 8 weekly). Pre-swap snapshot is mandatory and gated in the `BlueGreenCollectionManager` (M-12) — no swap proceeds without a verified fresh snapshot.
- Snapshot integrity: each snapshot records point count + a sampled checksum vs. Postgres `document_chunks` count for that scope; a >0.5% divergence fails the snapshot and pages.
- Restore tooling: `pmosctl qdrant restore --collection --snapshot` performs import + post-restore reconcile-verify against Postgres before the collection is marked live.

**Operational Procedures.**
- *Routine:* verify snapshot success daily (dashboard); run a **quarterly full restore drill** into a scratch namespace and time it against the 30-min Tier-2 RTO. A drill that misses RTO is a release-blocking action item.
- *Blue/green model migration (resolves review 2.8, 8.3):* per-tenant cutover, not a global flip; dual-read shadow-eval the new collection against the live one on a sampled query set; promote a tenant only when retrieval-quality delta ≥ 0; budget the re-embed as a discrete, scheduled cost event, throttled to the batch lane so it never contends with live ingestion.

**Monitoring.** SLIs: snapshot success rate, snapshot age (freshness), replica health/count per shard, restore-drill RTO, post-restore reconcile divergence %, query p50/p99 against each collection.

**Alerting.** Snapshot age > 8h → warning; > 14h → page (RPO at risk). Replica count < 2 on any shard → page. Reconcile divergence > 0.5% → page (security-adjacent: drift can mean stale ACLs in payload). Restore-drill RTO miss → ticket + release gate.

**Recovery.**
- *Single node loss:* automatic via replica; confirm replica re-creation; no customer impact.
- *Collection corruption:* engage per-tenant or per-collection kill switch (degrade affected tenants' Ask to "search unavailable, evidence cached" badge) → restore latest snapshot → reconcile-verify → release kill switch.
- *Total Qdrant loss:* restore all collections from snapshot in parallel; only tenants whose snapshots fail integrity fall through to Tier-3 rebuild, prioritized live-lane-first.

---

## 2. Redis Backup and Recovery Strategy

**Issue.** The review (2.1) found Redis persistence, replication, and failover unspecified despite Redis carrying three roles — cache, Redis Streams (in-flight events behind the outbox), and BullMQ (in-flight work).

**Risk.** A Redis failover without persistence loses in-flight Stream entries and queued jobs. The outbox invariant protects *durability of the source of truth* (events are reconstructable from the `outbox` table), but unprocessed Stream entries and running jobs would need replay; without a defined replay, work silently vanishes. A cache flush during failover also stampedes Postgres, threatening the interactive read budgets.

**Solution.** Split Redis by role and give each role the durability appropriate to it (this also resolves review 4.1's coupling risk):
- **Cache Redis:** ephemeral by design; AOF off; protected by replica + request-coalescing to prevent stampede on cold start.
- **Streams Redis:** AOF `everysec` + replica + Sentinel (or managed equivalent); but the **outbox table is the true source of durability** — the relay (M-04) is idempotent on `outbox.id`, so the recovery model is "re-drain the outbox from the last acknowledged offset," not "trust Redis to have kept everything."
- **Queue Redis (BullMQ):** AOF `everysec` + replica; jobs are idempotent (run-dispatch dedupe already exists, M-17) so redelivery is safe.

**Implementation.**
- Three logical Redis deployments (cache / streams / queue), each replica-backed across AZs, managed failover (Sentinel or cloud-managed). Year-1 may co-locate physically but must be logically separate instances with independent eviction/persistence config — the split is not deferred.
- Relay offset tracking: the relay persists its last-published `outbox.id` in Postgres; on Streams-Redis loss, it resumes from that offset and re-publishes; consumers are already idempotent.
- Cache: per-key TTLs, `maxmemory-policy allkeys-lru`, and a coalescing read-through (single-flight) so a cold cache does not stampede Postgres.

**Operational Procedures.** Quarterly failover drill per Redis role. After any Streams-Redis failover, run `pmosctl outbox verify-drained` to confirm no `outbox` rows are stuck unpublished beyond the relay-lag SLO. Cache warm-up script for planned cache-Redis restarts (pre-load Brief + hot-tenant projections).

**Monitoring.** SLIs per role: memory headroom, evicted-keys/sec (cache), replication lag, AOF fsync latency, Sentinel failover events, queue depth + oldest-job age (BullMQ), Stream consumer-group lag.

**Alerting.** Replication lag > 5s → warning, > 30s → page. AOF disabled on Streams/Queue Redis → page (config drift). Cache eviction rate spike correlated with Postgres CPU rise → warning (stampede risk). Queue oldest-job age > task-class budget → page. Failover event → informational page (confirm health).

**Recovery.**
- *Cache loss:* accept cold cache; coalescing limits Postgres load; warm-up script; no data loss.
- *Streams Redis loss:* failover to replica; if data lost, relay re-drains outbox from last offset; verify with `outbox verify-drained`.
- *Queue Redis loss:* failover to replica; redelivered jobs are deduped; verify no double-metering via the usage-rollup idempotency check.

---

# Part II — Postgres at Scale

---

## 3. Postgres Partitioning Strategy

**Issue.** The review (2.2, 6.1) found `organization_id` called a "ready-made shard key" with no statement of *when/how* the high-write tables are partitioned. The hottest tables — `outbox`, `agent_run_steps`, `messages`, `document_chunks`, and the append-only audit/meter tables — grow unbounded under 50M ingestion events/day and 3M agent tasks/day.

**Risk.** Table bloat, autovacuum falling behind, index bloat, and degrading OLTP latency that breaches the interactive read budgets (Peek <100ms, Sheet <150ms) — well before the Year-2 cell migration relieves anything.

**Solution.** Declarative partitioning now, chosen per table by its dominant access pattern:
- **Time-range partitioning** for append-mostly, time-queried, TTL-eligible tables: `outbox`, `agent_run_steps`, `messages`, audit tables, `billing_meters`. Monthly (or weekly for the highest-volume) partitions enable cheap drop/archival of old partitions and bound autovacuum scope.
- **Hash partitioning on `organization_id`** for large tenant-distributed content tables where queries are always tenant-scoped: `document_chunks` (the largest). This also pre-stages the cell shard key.
- **Composite** where both matter: `agent_run_steps` partitioned by time, sub-clustered by `organization_id`.

**Implementation.**
- PostgreSQL 16 native declarative partitioning. A `partition-manager-worker` pre-creates next-period partitions (never create-on-write) and detaches/archives expired ones.
- RLS policies and the `FORCE ROW LEVEL SECURITY` posture are applied to partitioned parents and inherited — verified by a migration test that asserts every partition enforces the canonical policy (a partition without RLS is the exact gap a cross-tenant probe must also catch).
- Connection topology (resolves the review's PgBouncer gap): PgBouncer in **transaction** pooling mode (required for `SET LOCAL app.current_org_id` safety), one pool per service group, pool sizes derived from `(Postgres max_connections − reserve) ÷ services`; documented sizing table in Part III. Background `BYPASSRLS` workers use a separate, smaller, audited pool.

**Operational Procedures.** Monthly partition-health review (sizes, bloat, autovacuum lag per partition). Pre-create horizon ≥ 2 periods. Index creation on large tables uses `CREATE INDEX CONCURRENTLY`. Quarterly `pg_repack` on any table exceeding bloat threshold.

**Monitoring.** SLIs: per-table/partition dead-tuple ratio, autovacuum lag, partition count vs. horizon, longest transaction age, replication lag to read replicas, connection-pool saturation per service.

**Alerting.** Autovacuum lag on a hot partition beyond threshold → warning. Dead-tuple ratio > 20% on a top-5 table → warning, > 40% → page. Pool saturation > 85% sustained → page. Next-period partition missing within 48h of period roll → page (data-loss risk on insert).

**Recovery.** Postgres is the system of record: streaming replication + PITR (WAL archiving to S3); RPO ≤5 min via continuous archiving; RTO ≤1h via promote-replica. Restore drills quarterly. A runaway partition (unexpected hot tenant) is mitigated by detaching it to its own tablespace or fast-tracking that tenant to a dedicated Qdrant collection + (Year-2) cell.

---

## 4. Outbox Retention Strategy

**Issue.** The review (2.2, 4.3) found no retention/archival policy for the `outbox` table and flagged the single relay-worker as a throughput chokepoint whose lag directly drives the visible freshness badge.

**Risk.** Unbounded `outbox` growth becomes the hottest table in the system; relay lag turns "honest degradation" into "constant degradation"; a slow relay silently delays every projection and contradiction-detection path.

**Solution.** Bounded, partitioned, horizontally-drained outbox with explicit lag SLOs:
- **Retention:** an outbox row's job is done once published *and* acknowledged by all projection consumers. Published rows are retained 7 days (for replay/debugging) in time partitions, then dropped; the durable history lives in the **event archive in S3** (the rebuildable-projections source), not in the hot table.
- **Horizontal drain (resolves 4.3):** partition outbox consumption by `hash(organization_id) mod N` across N relay workers, so relay throughput scales linearly and one large tenant cannot monopolize the relay. Idempotent publish on `outbox.id` is preserved.

**Implementation.**
- `outbox` time-partitioned (daily); a `published_at` + per-consumer high-water mark drives the drop policy. `outbox-archiver-worker` streams published rows to `s3://pmos-event-archive/{cell}/{day}` before partition drop.
- N relay workers (start N=4 per cell, scale on lag) consuming disjoint `organization_id` hash ranges; the relay's last-published offset persists in Postgres (ties to §2 recovery).
- Projection rebuild path: documented `pmosctl projection rebuild --from s3://… --since <ts>` reconstructs any projection in minutes from the S3 archive — the capability the spec promises, now with a concrete tool.

**Operational Procedures.** Watch relay lag as the primary freshness driver. Scale relay worker count when sustained lag approaches the SLO. Verify archiver keeps pace before partition drop (never drop an unarchived partition). Test projection rebuild quarterly.

**Monitoring.** SLIs: relay lag (publish time − row create time) p50/p99 per hash range, outbox unpublished backlog, archiver lag, partition count, projection-rebuild duration (drill).

**Alerting.** Relay lag p99 > 2 min → warning (freshness badge will show); > 10 min → page (live contradiction detection delayed). Unpublished backlog growing monotonically for > 5 min → page (relay stuck). Unarchived partition approaching drop → page (would lose replay source).

**Recovery.** Relay stuck → restart/scale workers, resume from persisted offset (idempotent). Lost projection → rebuild from S3 archive. Outbox backlog spike → temporarily add relay workers; if a single tenant is the cause, throttle that tenant's ingestion lane.

---

# Part III placeholder note

*(The consolidated SLO/SLI/error-budget/RTO-RPO/threshold tables appear in Part VI below, after all sixteen sections, so each section's numbers are defined in context first.)*

---

# Part III — Agent Runtime Safety

---

## 5. Agent Runtime Limits

**Issue.** The review (2.3) found no per-run ceilings: no max steps, no max tokens, no runaway-cost circuit-breaker on a single run.

**Risk.** A looping, stuck, or adversarially-prompted agent burns tokens against a tenant's meter and platform COGS until a human notices — both a cost risk (§7/§8) and a reliability risk.

**Solution.** Hard, per-task-class resource ceilings enforced at the orchestrator (M-17) and the Model Gateway (M-07), independent of the autonomy level. Limits are configuration on `ai_agents`/`autonomy_matrix`, versioned with the agent and gated by the eval harness.

**Implementation.**
- Per-(agent, task-class) ceilings: `max_steps`, `max_tokens_total`, `max_tool_calls`, `max_wall_clock`. Defaults (tunable): drafting tasks 25 steps / 150k tokens / 10 min; research/synthesis 60 steps / 500k tokens / 20 min; system-agent background 40 steps / 300k tokens / 15 min.
- Enforcement is two-layer: the orchestrator counts steps/tool-calls and aborts; the Model Gateway enforces the token ceiling as a hard stop even if the orchestrator misbehaves (defense-in-depth, mirroring the security posture).
- On breach: the run is failed with `run.aborted_limit`, the partial work is checkpointed (so it is replayable/inspectable), and the failure surfaces as honest degradation to the requesting human.

**Operational Procedures.** Review limit-breach rate weekly per agent/task-class; a rising breach rate is a signal of either a prompt regression or a model regression (ties to §10 release gates). Limits are changed only through the agent-versioning + eval-gate path, never hot-edited in prod.

**Monitoring.** SLIs: per-task-class step/token/tool-call distributions (p50/p95/p99), limit-breach rate, aborted-run rate, tokens-per-accepted-artifact.

**Alerting.** Limit-breach rate for any (agent, task-class) > 2% over 1h → warning; > 5% → page (likely regression or attack). A single run hitting the token ceiling → logged; a *spike* of ceiling hits → page.

**Recovery.** Aborted runs are checkpointed; the human sees a clear "the agent stopped at its safety limit" message and can resume with adjusted scope or escalate. If breaches stem from a model swap, roll back the model binding (§10).

---

## 6. Agent Timeout Policies

**Issue.** The review (2.3) noted there is a `job-reaper-worker` for stale jobs but no agent-run wall-clock timeout policy distinct from generic job reaping.

**Risk.** A run that hangs on a slow tool or provider call holds resources, blocks a human waiting on an approval surface, and (if interactive) burns the SSE connection budget.

**Solution.** Layered timeouts: per-tool-call, per-step, and per-run wall-clock, each with a defined action, coordinated with the SSE 15-min server-termination already in the contract.

**Implementation.**
- Per-tool-call timeout (default 30s; model calls 120s) with one bounded retry on transient failure (exponential backoff, jitter) then fail-the-step.
- Per-step timeout = tool timeout + verification budget.
- Per-run wall-clock = the §5 `max_wall_clock`; on expiry the run aborts-with-checkpoint.
- Interactive runs: SSE terminates server-side at 15 min with `Last-Event-ID` resume; a long run continues server-side and the client re-attaches — the run is not killed by the SSE boundary, only the connection is recycled.
- Heartbeat/liveness: each run emits a step heartbeat; a run with no progress for 2× its expected step time is declared stuck and reaped.

**Operational Procedures.** Distinguish "slow" (within budget, badge it) from "stuck" (no heartbeat → reap). Track provider-call latency separately so a provider slowdown triggers Model-Gateway failover (lower tier + badge) rather than mass run timeouts.

**Monitoring.** SLIs: run wall-clock distribution, tool-call latency by tool/provider, stuck-run reap rate, SSE reconnect rate, approval-surface wait time.

**Alerting.** Stuck-run reap rate > 1% over 1h → warning. Tool/provider p99 latency beyond budget sustained → warning + auto-failover check. Mass run-timeout event (>10 runs/min hitting wall-clock) → page (provider or dependency outage).

**Recovery.** Stuck run → reap + checkpoint + honest message. Provider slowness → Model-Gateway failover to lower tier with visible quality badge (existing mechanism, now with a trigger threshold). Dependency outage → engage relevant kill switch, drain interactive runs to a "agents paused — degraded" badge.

---

# Part IV — Cost Control

---

## 7. AI Cost Controls

**Issue.** The review (2.4, 8.1, 5.5, 8.2) found metering is excellent but there is **no real-time spend cap** and that per-artifact cost is *several* model calls (contrarian probe + groundedness verifier + tiered cascade + reranker) not modeled as such.

**Risk.** Metering ≠ capping. A runaway tenant, a bug, or an abusive prompt can run up unbounded model cost in real time, blowing platform COGS for a period and producing surprise invoices that churn customers. The COGS-per-task budget and the "consumption ≥35% of revenue" target are unsafe if built on single-call cost estimates.

**Solution.** Real-time cost governance at the Model Gateway, in addition to (not instead of) the append-only meter:
- **Pre-flight estimate + post-flight true-up:** every model call is estimated before dispatch and reconciled after; the running per-(tenant, period) and per-run spend is tracked in hot state (Queue/cache Redis) and checked against caps before each call.
- **Correct COGS model:** the per-artifact cost model explicitly sums probe + generation + verifier + rerank + retrieval-cascade calls (resolves 5.5/8.2). The metering already captures this per `agent_run`; the *budgeting* must use the same multi-call basis.
- **Tiered enforcement:** soft alert → throttle to batch lane → hard cap (block with clear message) — honest degradation, never a silent stop.

**Implementation.**
- `CostGovernor` service in the Model-Gateway path: reads per-tenant budget (`PlanBinding` + configured caps), maintains running spend in hot state, and gates each call. The authoritative spend remains the append-only `billing_meters` (M-34); hot state is an optimization reconciled against the meter by the `usage-rollup-worker` (already idempotent, so no double-count).
- Caps are layered: per-run cap (§5 token ceiling in dollars), per-tenant-per-day soft + hard caps, and a platform-wide circuit breaker for correlated runaway (e.g., a bad model deploy).

**Operational Procedures.** Weekly COGS-per-accepted-artifact review against budget per agent/task-class. Any agent whose true multi-call COGS exceeds budget is a release-gate finding (§10). Re-embedding migrations (§1) are budgeted as discrete events, excluded from steady-state COGS alerts but tracked.

**Monitoring.** SLIs: COGS per accepted artifact (by agent/task-class), model-calls-per-artifact, tenant spend vs. cap (%), platform spend run-rate vs. budget, estimate-vs-actual error.

**Alerting.** Tenant at 80% of daily cap → notify owner (in-product + email); 100% → enforce (batch-lane or block) + notify. Platform run-rate > 1.2× budget for the period → page finance + eng. Estimate-vs-actual error > 15% → warning (estimator drift, fix before caps misfire).

**Recovery.** Runaway single run → §5 ceiling aborts it. Runaway tenant → daily hard cap engages, owner notified, support can raise the cap deliberately. Correlated runaway (bad deploy) → platform circuit breaker pauses non-interactive agent work, page, roll back the offending model/agent version (§10).

---

## 8. Tenant Budget Enforcement

**Issue.** Same root finding as §7 (2.4/8.1), viewed from the commercial/tenant side: consumption pricing without a cap is a CFO's nightmare on both sides of the table.

**Risk.** A customer receives an unexpectedly large invoice and churns or disputes; or PMOS absorbs unbilled overage. Either erodes the consumption-pricing thesis.

**Solution.** Make the tenant budget a first-class, owner-controlled, visible object with predictable behavior at the boundary — the predictability that makes metered pricing acceptable (resolves the spirit of Open-Question Q4).
- Each tenant has: a plan (`platform_fee | consumption | hybrid`), an optional **autonomy-unit budget** per period, soft-alert thresholds, and a hard-cap behavior chosen by the owner (`block` or `allow-overage-with-alert`).
- Budgets and live consumption are surfaced in-product (the metering surface), not discovered on the invoice.

**Implementation.**
- Extends §7's `CostGovernor` with tenant-facing semantics: budget config on the `organizations`/`PlanBinding` records; live usage from `UsageRollup`; enforcement actions identical to §7's tiers.
- Owner-only controls (RBAC `owner`): set budget, set boundary behavior, raise cap (audited, hash-chained — a budget change is a decision).
- Viewers and editors are never blocked from *reading*; only autonomy-unit-consuming *work* is gated (read scaling is cheap by design, so reads are never the cost lever).

**Operational Procedures.** Onboarding sets a default conservative cap with the customer, not a silent unlimited. Monthly review of tenants repeatedly hitting caps (a signal to right-size their plan, a healthy expansion conversation, not a surprise).

**Monitoring.** SLIs: per-tenant consumption vs. budget, count of tenants in soft/hard zones, overage incidents, cap-raise frequency, invoice-dispute rate.

**Alerting.** Tenant crosses soft threshold → owner notified (in-product + email). Hard cap reached → enforcement + owner notified + CS flagged. Repeated month-over-month hard-cap hits → CS expansion signal (not a page).

**Recovery.** A disputed overage is resolved against the append-only, hash-chained, idempotent meter — the audit fabric is the inspectable source of truth, by design. A wrongly-blocked tenant is unblocked by an owner cap-raise (audited), effective immediately.

---

# Part V — Quality Gates & AI Trust

---

## 9. Evaluation Thresholds

**Issue.** The review (2.5, 5.1) found the eval harness "gates releases" with no published numeric pass/fail bands, and the groundedness verifier — on which the ≥95% honest-abstention metric and the "every sentence is a contract" claim rest — was a named step, not a specified mechanism.

**Risk.** "The eval harness gates releases" is unfalsifiable without numbers; teams ship to vibes. The central trust claim rests on an unspecified component whose own error rate is unknown.

**Solution.** Two things: (a) specify the groundedness verifier as a concrete, independently-evaluated mechanism, and (b) publish numeric eval thresholds per (agent, task-class), versioned with the agent.

**Groundedness verifier (resolves 5.1).** A two-stage, cost-aware mechanism rather than a blanket LLM-judge on every sentence:
- Stage 1 — *retrieval-overlap / NLI check* (cheap, fast, in-path): each claim's text is checked for entailment against its cited evidence spans using a small NLI model. Claims that pass with high entailment are accepted; claims below threshold are escalated.
- Stage 2 — *LLM adjudication* (only for escalated claims): a frontier-tier judge confirms or marks the claim `inference`/withholds it.
- This keeps the verifier within the Ask first-token budget (most claims clear Stage 1) while bounding cost. The verifier has its **own eval set** with a labeled gold corpus and a published target (verifier precision/recall on "ungrounded" detection ≥ 0.95 / ≥ 0.90).

**Numeric thresholds (illustrative bands; tuned per tenant over time, but these are the release floors):**

| Metric (per agent/task-class) | Release floor | Kill/pivot |
|---|---|---|
| Groundedness (share of claims passing verification) | ≥ 0.97 | < 0.90 |
| Honest abstention accuracy (unanswerable set) | ≥ 0.95 | < 0.90 |
| Citation validity (citation resolves & supports) | ≥ 0.98 | < 0.95 |
| Calibration error (ECE) on confidence | ≤ 0.10 | > 0.15 |
| Story edit-distance (normalized, accepted artifacts) | ≤ 0.20 | > 0.30 (spec trigger) |
| Tool-call injection attack-success-rate | 0 (absolute) | any > 0 |
| Contrarian-probe presence (drafting/prioritization) | 100% | any miss |

**Implementation.** The eval harness (M-20) runs these as a suite per agent version against a versioned gold corpus; results are stored and attached to the agent version. The injection corpus is a living, versioned asset (resolves review 3.1): a minimum case count per tool, mandatory expansion on every new tool/parameter, owned by security engineering, with a documented methodology a reviewer can inspect. "Attack-success-rate 0" is meaningful only against a corpus of stated size and breadth.

**Operational Procedures.** No agent version reaches prod without a passing eval record. Gold corpora are reviewed quarterly and expanded from real misses (closing the loop). Per-tenant threshold tightening happens via the owned learning loop, never loosening below the release floor.

**Monitoring.** SLIs: live (production) groundedness sample rate and pass rate, abstention accuracy on shadow unanswerable probes, citation-validity sampling, ECE tracking, edit-distance per (team, task-type).

**Alerting.** Live groundedness pass rate dips below floor on a sustained window → page (possible silent regression). Edit-distance trending toward 0.30 over a quarter → product-quality review (the spec's kill/pivot signal). Any tool-call injection success in prod or CI → sev-1, halt promotions.

**Recovery.** A failing live metric triggers model/agent rollback (§10) and, if security-related, kill-switch on the affected tool/agent.

---

## 10. Release Gate Criteria

**Issue.** Implied by 2.5 and the review's AI-risk 5.4: "improves overnight with zero migration" is optimistic — a model swap can regress a tenant even while improving on average — and the release gate itself was not defined as a procedure.

**Risk.** A silent quality or cost regression ships because the swap was a config flip, not a gated promotion.

**Solution.** Every change that can affect output quality, safety, or cost — agent prompt/version, model binding, routing policy, retrieval config — is promoted only through a gated pipeline. Model swaps are *mechanical but not unguarded* (resolves 5.4).

**Implementation — the gate (all must pass):**
1. Eval-harness suite (§9) meets every release floor for the affected agents/task-classes.
2. Injection corpus: attack-success-rate 0.
3. COGS-per-accepted-artifact (§7) within budget on the eval set.
4. Latency budgets (§ Part VI) met on a representative tenant, including the largest (resolves review 5.2 — the verifier and retrieval stages are measured in-path).
5. Shadow run: the new version runs in shadow against a sample of live traffic; quality delta ≥ 0 and no safety regressions before promotion.
6. Per-tenant canary for model swaps: promote to a small cohort, monitor live SLIs, then ramp.

**Operational Procedures.** Promotions are append-only, hash-chained decisions (a release is a decision — consistent with PMOS being self-hosting). Rollback is a single command reverting the model/agent binding; because runs are stateless and bindings are config, rollback is immediate. A failed canary auto-halts the ramp.

**Monitoring.** SLIs: gate pass/fail history, shadow-vs-live quality delta, canary cohort SLIs, time-to-rollback (drill).

**Alerting.** Canary SLI regression beyond tolerance → auto-halt ramp + page. Gate bypass attempt (deploy without eval record) → block + page (process integrity).

**Recovery.** Immediate binding rollback; the prior version's eval record proves the safe state. Post-incident, the regression becomes a new gold-corpus case.

---

# Part VI — Connectors, Freshness, Security

---

## 11. Connector Outage Handling

**Issue.** The review (2.6) found connector failure semantics under-specified: sustained source outage, backfill-after-outage ordering vs. the live lane, and stale-ACL window surfacing were undefined.

**Risk.** During a Zendesk/Jira/Gong outage, freshness silently lapses or ACLs drift past the ≤1h SLO without a visible badge — the latter being a security exposure (stale ACL = over-exposure), not just a quality gap.

**Solution.** Per-connector health states with defined behavior and honest surfacing at each state, and a catch-up policy that protects both the live lane and the ACL guarantee.

**Implementation.**
- Connector health states: `healthy → degraded (elevated errors / webhook gap) → down (no successful sync > threshold)`. State is on `connector_health` (M-08), surfaced in the Connectors admin and as a freshness signal on affected reads.
- Webhook-gap detection: a missed-heartbeat / sequence-gap detector triggers CDC-poll catch-up (`cdc-poller-worker`) without waiting for the next scheduled poll.
- Catch-up ordering: post-outage backfill runs in the **bulk lane** and is **ACL-first** — ACL reconciliation is prioritized over content so that the ≤1h ACL SLO is protected even when content lags; content backfill never preempts live-lane ingestion for other sources.
- Stale-ACL safety: if ACL data for a source is older than the SLO, retrieval **fails closed** for that source's content (treated as "sources withheld") and the read surfaces "n sources withheld — connector degraded," consistent with honest abstention. This resolves review 10.4's fallback-policy gap: fail-closed by default, coverage surfaced honestly.

**Operational Procedures.** On `down`, notify tenant admins with scope ("Jira sync degraded; X surfaces may be stale"). On recovery, verify ACL reconciliation completed before lifting the stale-ACL fail-closed. Track per-connector SLA against the provider.

**Monitoring.** SLIs: per-connector sync success rate, webhook-gap incidents, freshness age per source, ACL-reconciliation age per source, backfill completion time.

**Alerting.** Connector `degraded` > 15 min → warning + admin notice. `down` → page on-call + admin notice. ACL-reconciliation age approaching SLO → warning; exceeding SLO → page (security-adjacent) + auto fail-closed engaged.

**Recovery.** Restore connector → ACL-first catch-up → verify reconciliation → lift fail-closed → content backfill in bulk lane → freshness badges clear as lanes drain.

---

## 12. Freshness Degradation Handling

**Issue.** Tied to review 4.3 and Principle 8: freshness is driven by relay lag and connector health, and the badge is the safety valve — but the *thresholds* at which degradation is declared, and the operator response, were unspecified.

**Risk.** Either the badge never fires (silent staleness — the cardinal sin) or it fires constantly (alarm fatigue — review 7.3), both eroding trust.

**Solution.** A single, well-defined freshness model with thresholds tuned so badges appear on *material* degradation only, computed by the existing `FreshnessTracker` (M-04) from relay lag + connector health + reconciler status.

**Implementation.**
- Freshness state per read surface = worst of: relay lag, source connector freshness, projection rebuild status. States: `fresh (< SLO) → lagging (SLO–2×SLO, subtle badge) → stale (> 2×SLO, prominent badge + reason)`.
- The Brief (re-rendered from the ledger, never stored stale) carries the freshness of its inputs; a stale input is shown as such rather than omitted.
- Reconciler status feeds freshness: a failed nightly Postgres↔Qdrant reconciler (review 6.4) degrades affected surfaces and pages, because stale index = wrong answers.

**Operational Procedures.** Tune thresholds against real traffic to keep badge frequency low (target: < 2% of reads show any badge in steady state). A rising badge rate is itself an SLI of platform health, not just a UX element.

**Monitoring.** SLIs: % of reads by freshness state, freshness-age distribution per surface, reconciler success/lag, badge-display rate.

**Alerting.** Badge-display rate > 5% of reads over 1h → page (systemic freshness problem). Reconciler failure/miss → page (security-adjacent for ACL/index drift). `stale` state on the Brief at the 6am publish window → page (SLO-visible).

**Recovery.** Trace to root (relay, connector, or reconciler), remediate per the relevant section, badges clear automatically as freshness recovers. Resolves review 6.4 by making reconciler health a paged SLO, not a silent nightly job.

---

## 13. Intra-Tenant Security Validation

**Issue.** The review (1.4, 3.2) flagged that the org boundary is hard RLS but the workspace/Stream boundary is "soft" app-layer ABAC — exactly where a missing predicate leaks *within* a tenant (a PM seeing another team's unreleased strategy), a real enterprise sale-killer.

**Risk.** A within-tenant over-exposure bug ships undetected because, unlike the cross-tenant case, there is no independent probe testing it.

**Solution.** Promote the intra-tenant boundary to a first-class, separately-tested control with its own continuous probe and release-gate coverage — the intra-tenant analogue of the nightly cross-tenant probe.

**Implementation.**
- **Intra-tenant access probe** (`intra-tenant-probe-worker`, nightly): for a matrix of (user, stream, sensitivity) fixtures per tenant, asserts that ABAC narrowing (`stream_ids`, `max_sensitivity`) yields exactly the expected visible set; any over-return is a sev-1.
- **Release-gate ABAC tests:** the ABAC trim (M-02) and lens-share path (M-36) carry a mandatory test suite asserting "a shared lens never widens what a recipient may see"; a failing test blocks release.
- **Selective RLS on `workspace_id`** for the highest-sensitivity tables (unreleased strategy, pre-announcement decisions): measure the plan-degradation cost the spec worried about; on the *small set* of sensitive tables it is likely acceptable and converts a soft boundary to hard. Decide per-table with measured query plans, documented as an ADR.
- ACL drift for intra-tenant sharing reconciled on the same ≤1h cadence as source ACLs.

**Operational Procedures.** Treat any intra-tenant probe hit exactly as seriously as cross-tenant for *that tenant's* trust (sev-1; notify security; root-cause). Review ABAC test coverage whenever a new shareable surface is added.

**Monitoring.** SLIs: intra-tenant probe pass rate, ABAC test coverage of shareable surfaces, lens-share denials vs. grants (anomaly detection), sensitive-table RLS overhead.

**Alerting.** Any intra-tenant probe over-return → sev-1 page. ABAC test failure in CI → block release. Anomalous spike in cross-stream reads by a single principal → security review.

**Recovery.** Engage per-stream or per-tenant kill switch to contain → patch the predicate/policy → re-run the probe to confirm closure → notify the affected tenant per the incident policy (§16). A confirmed intra-tenant leak follows the same disclosure discipline as cross-tenant for the affected customer.

---

## 14. Stream Ticket Rotation and Replay Protection

**Issue.** The review (3.3) found single-use 60s stream tickets and JWKS-verified JWTs are good, but JWT lifetime/rotation, revocation latency on SCIM deprovisioning, and ticket-to-connection binding were unspecified.

**Risk.** A deprovisioned editor retains access until token expiry; a leaked stream ticket within its 60s window is replayable if not bound to a single connection.

**Solution.** Short-lived JWTs with refresh, server-side immediate revocation on deprovision, and connection-bound single-use tickets.

**Implementation.**
- **JWT lifetime:** access token TTL ≤ 10 min, refresh via Clerk; the BFF caches JWKS and validates `exp`/`nbf`/`iat`.
- **Immediate revocation:** a server-side session denylist (keyed on `user_id`/`session_id`) checked at the BFF; SCIM deprovision (M-02 `scim-sync-worker`) writes to the denylist synchronously, so a deprovisioned user is blocked within seconds regardless of JWT `exp`. Denylist lives in cache Redis with a TTL = max JWT lifetime (a denylisted entry can expire once no valid token could remain).
- **Stream-ticket hardening:** the single-use 60s ticket is bound to (user, intended stream resource, and a connection nonce); the SSE handler validates the nonce on connect and burns the ticket; a replay presents a burned/wrong-nonce ticket and is rejected.
- Step-up MFA remains required for identity-binding writes (already specified).

**Operational Procedures.** Verify denylist propagation latency in the SCIM drill (deprovision a test user; confirm block within target). Rotate signing keys per Clerk policy; confirm BFF JWKS refresh picks up rotation without downtime.

**Monitoring.** SLIs: denylist propagation latency (deprovision → block), ticket-replay rejection count, JWT validation failure rate, refresh success rate.

**Alerting.** Denylist propagation latency > 30s → page (deprovision not effective). Spike in ticket-replay rejections → security review (possible token leakage/abuse). JWKS refresh failure → page (auth integrity).

**Recovery.** Suspected token compromise → force-revoke the user's sessions (denylist) + require re-auth. Signing-key compromise → rotate keys, invalidate all tokens (mass re-auth), communicate per incident policy.

---

# Part VII — Operations

---

## 15. Operational Runbooks

**Issue.** The review (9.1, 9.2) found observability *named* (OTel linkage, SLOs) but not specified as a system, and no runbooks for the named failure modes — the single biggest operational gap (Observability scored 4/10).

**Risk.** A 42-module, 8-service-group, 7-datastore system cannot be operated on trace-linkage alone; the SLOs are unenforceable without dashboards, an alert catalog, and procedures. An unrehearsed kill switch is itself a liability.

**Solution.** Specify the observability platform and a runbook per failure mode.

**Observability platform.**
- **Tracing:** OpenTelemetry end-to-end (already the audit-linkage substrate); tail-based sampling (keep all errors + slow traces + a baseline sample). Every request carries `trace_id`/`request_id` (already in the error envelope).
- **Metrics:** a Prometheus-compatible metrics pipeline; every SLI in Part VI is a recorded metric with a dashboard panel.
- **Logs:** structured JSON logs, tenant-tagged (`organization_id`), aggregated and queryable, with PII-screening on log content (never log raw tenant content to provider-retained sinks — ties to ZDR).
- **Dashboards:** one per service group + one per SLO + a platform overview (the "is PMOS healthy right now" board) + a per-tenant health board for support.

**Runbook set (minimum, one per failure mode).** Each runbook: symptom → dashboard to open → diagnosis steps → remediation → verification → comms. Required runbooks: Qdrant node loss / corruption / total loss (§1); Redis failover per role (§2); Postgres failover + PITR (§3); outbox relay stuck / lag (§4); agent runaway / mass timeout (§5–6); cost circuit-breaker engaged (§7); connector outage + ACL fail-closed (§11); freshness systemic degradation (§12); intra-tenant probe hit (§13); cross-tenant probe hit (existing sev-0); token/key compromise (§14); model-swap regression rollback (§10); kill-switch engage/release per scope.

**Operational Procedures.** Each runbook is tested in a game-day at least once before GA and quarterly after. A failure mode without a tested runbook is a release-gate finding. Kill switches (per tenant/agent/tool/level) each have an explicit "when to engage / how to verify effect / how to release" procedure — engaging one must instantly stop token issuance (M-18) and tool execution (M-19), verified in the game-day.

**Monitoring.** Meta-SLI: runbook coverage (% of named failure modes with a tested runbook; target 100% pre-GA), game-day pass rate, mean-time-to-diagnose in drills.

**Alerting.** This section *defines* the alert catalog — see Part VI tables; every alert names its runbook.

**Recovery.** Recovery *is* the runbook content; this section guarantees one exists, is discoverable (linked from the alert), and is tested.

---

## 16. Incident Response Procedures

**Issue.** Implied across 9.x: severity levels are referenced (sev-0 cross-tenant, sev-1 audit-verify mismatch) but there is no unified incident-response procedure, especially for the security-critical and customer-affecting cases.

**Risk.** Inconsistent response, unclear ownership, missed customer/regulator obligations (e.g., breach disclosure, GDPR), and no post-incident learning loop.

**Solution.** A single incident-response framework with severity definitions, roles, communication paths, and a mandatory post-incident review feeding the eval/gold corpora and runbooks.

**Implementation — severity ladder.**

| Sev | Definition | Examples | Response |
|---|---|---|---|
| **Sev-0** | Existential / cross-tenant exposure or platform-down | Cross-tenant probe hit; confirmed data leak; total platform outage | Immediate page, incident commander, kill switches, customer + (if breach) regulator comms within obligation window |
| **Sev-1** | Security-adjacent or major customer impact | Intra-tenant over-exposure; audit-chain verify mismatch; ACL fail-closed at scale; tool-call injection success | Page, IC, contain (kill switch), root-cause, affected-customer comms |
| **Sev-2** | Significant degradation, SLO breach | Relay lag paging; connector down; cost circuit-breaker; freshness systemic | Page on-call, remediate per runbook, error-budget review |
| **Sev-3** | Minor / single-tenant / no data risk | Single stuck run; one connector degraded | Ticket, business-hours fix |

**Roles.** Incident Commander (coordinates), Ops lead (remediates), Comms lead (customer/internal updates), Scribe (timeline). Security engineering is auto-paged on any sev-0/sev-1 with a security dimension.

**Operational Procedures.** Declare → assess severity → assign IC → contain → remediate (runbook) → verify → communicate → **post-incident review (blameless)** within 5 business days. The review produces: timeline, root cause, action items (owned, dated), and — where relevant — a new gold-corpus case (§9), a new/updated runbook (§15), or a new probe fixture (§13). For data-exposure incidents, the GDPR/contractual disclosure clock and the erasure-cascade verification (resolves review 6.3 — confirm erasure covered *every* derived store: Postgres, Qdrant points, caches, projections, provider-side) are explicit checklist items.

**Monitoring.** SLIs: incident count by severity, MTTD, MTTR, error-budget burn per SLO, action-item closure rate, repeat-incident rate.

**Alerting.** Every Part-VI page is classified to a severity on creation and routes accordingly. Error-budget burn-rate alerts (fast-burn / slow-burn) page before the budget is exhausted (see Part VI).

**Recovery.** Recovery is per-runbook; incident response guarantees coordination, comms, and that the system *learns* — closing the loop the architecture review identified as missing.

---

# Part VI (consolidated) — SLOs, SLIs, Error Budgets, Recovery Objectives, Alert Thresholds

*This is the liftable config. SLOs are the promise; SLIs are what's measured; error budgets convert the SLO into a spend; RTO/RPO are the recovery promise; alert thresholds are when to act. Targets align with Master Spec §13/§18 where stated and fill the gaps the review found.*

## A. Service-Level Objectives & Indicating SLIs

| Domain | SLO | SLI (measured) | Error budget |
|---|---|---|---|
| Interactive availability | 99.9% / 30d | successful interactive requests ÷ total | 43m 12s / 30d |
| Line "Go" latency | p99 < 50ms | BFF Go handler latency | 1% of requests may exceed |
| Peek latency | p99 < 100ms | Peek handler latency | 1% |
| Sheet (cached) latency | p99 < 150ms | Sheet read latency | 1% |
| Ask first token | p95 < 700ms | time to first SSE token | 5% |
| Provenance Lens | p99 < 400ms | provenance resolve latency | 1% |
| Cold load → interactive Brief | p95 < 1.5s | client-measured | 5% |
| Brief published by 6am local | ≥ 99.5% of days | Brief publish timestamp | ~1 missed day / ~7 months |
| Groundedness (live) | ≥ 0.97 pass | sampled claim verification | breach → page |
| Honest abstention | ≥ 0.95 | shadow unanswerable probes | breach → page |
| Tool-call injection success | 0 | CI + prod red-team | any > 0 → sev-1 |
| Relay freshness | p99 lag < 2 min | publish − create time | breach → badge + warning |
| ACL reconciliation age | < 1h | per-source ACL age | breach → fail-closed + page |
| Cross-tenant isolation | 0 leaks | nightly probe | any hit → sev-0 |
| Intra-tenant isolation | 0 over-returns | nightly probe | any hit → sev-1 |
| Per-task COGS | within budget | COGS per accepted artifact | breach → release-gate finding |

## B. Recovery Objectives (RTO / RPO) per datastore

| Store | RTO | RPO | Primary mechanism | Backstop |
|---|---|---|---|---|
| PostgreSQL (SoR) | ≤ 1h | ≤ 5 min | streaming replica promote + PITR (WAL→S3) | cross-region replica (Y2/cells) |
| Qdrant | ≤ 30 min (Tier-2) | ≤ 6h (snapshot) | replica (RF≥2) → S3 snapshot restore | rebuild from Postgres (hours) |
| Redis — streams | ≤ 5 min | ~0 (outbox is durable) | replica failover + outbox re-drain | replay from `outbox` table |
| Redis — queue | ≤ 5 min | seconds (AOF everysec) | replica failover; jobs idempotent | re-enqueue from source state |
| Redis — cache | ≤ 1 min | n/a (ephemeral) | cold start + coalescing | rebuild from Postgres/projections |
| S3 / WORM (audit, archive) | ≤ 1h | ~0 | cross-region replication, versioned | WORM immutability |

## C. Error-budget burn-rate alerting (multi-window)

| Burn rate | Window | Meaning | Action |
|---|---|---|---|
| 14.4× | 1h | budget exhausted in ~2 days | page (fast burn) |
| 6× | 6h | accelerated burn | page |
| 3× | 24h | sustained elevated burn | warning → review |
| 1× | 72h | steady erosion | ticket / trend review |

## D. Alert severity routing (summary)

| Trigger | Severity | Runbook (§) |
|---|---|---|
| Cross-tenant probe hit | Sev-0 | existing + 16 |
| Intra-tenant probe over-return | Sev-1 | 13 |
| Audit-chain verify mismatch | Sev-1 | 16 |
| Tool-call injection success | Sev-1 | 9, 16 |
| ACL reconciliation > 1h | Sev-1/2 | 11 |
| Relay lag p99 > 10 min | Sev-2 | 4 |
| Qdrant replica < 2 / corruption | Sev-2 | 1 |
| Redis replication lag > 30s | Sev-2 | 2 |
| Postgres pool saturation / autovacuum lag | Sev-2 | 3 |
| Platform cost run-rate > 1.2× budget | Sev-2 | 7 |
| Connector down | Sev-2 | 11 |
| Freshness badge rate > 5% | Sev-2 | 12 |
| Denylist propagation > 30s | Sev-2 | 14 |
| Agent limit-breach rate > 5% | Sev-2 | 5 |
| Single stuck run / one connector degraded | Sev-3 | 5/6, 11 |

---

# Closing: Does this make PMOS implementation-ready?

The architecture review's verdict was: greenlight the Foundation build, but gate production GA on seven operational items. This document closes those seven and the broader High/Medium set:

- **Observability as a system** (review's #1 gap) → §15 + Part VI: a defined tracing/metrics/logging platform, a dashboard per SLO, an alert catalog, and a tested runbook per failure mode.
- **Datastore DR + Postgres scaling** → §1–§4 + Part VI.B: real RTO/RPO per store, partitioning, retention, pooling.
- **Real-time spend caps** → §7–§8: capping, not just metering, with honest-degradation behavior at the boundary.
- **Groundedness verifier specified** → §9: a concrete two-stage mechanism with its own eval target.
- **Intra-tenant hardening + attack-corpus** → §13 + §9: a first-class probe, ABAC release gates, and a living injection corpus.
- **Numeric eval gates + erasure-cascade coverage** → §9–§10, §16.
- **Cell-migration de-risk** → folded into §3/§4 recovery (replay tooling exists and is drill-tested), to be exercised at small scale in Year-1 per the review.

**Remaining before GA is now execution, not specification:** build these mechanisms, wire the dashboards and alerts, write the runbooks from the templates here, and pass the game-days. The Low-severity findings (the `password` enum value, COUNT(*)/metric-store wording, Provenance-Underline density, UUIDv7 coupling ADR) are documentation/polish items that should be folded into the source corpus but do not gate GA.

**Implementation-ready assessment:** with this document adopted as authoritative for the run layer, PMOS moves from *design-ready* to *implementation-ready*. The gate to **production GA** becomes a checklist of *executed-and-drilled* (every Part-VI SLO instrumented, every §15 runbook game-day-passed, every §9 eval gate live), not a checklist of *unanswered design questions*. That is the right place to be: nothing here requires rethinking the architecture; all of it requires building and rehearsing what the architecture already implies.

---

*This document is itself amendable as a decision: any change to a threshold, RTO/RPO, or procedure is recorded with author, rationale, and date, consistent with PMOS's self-hosting principle. Thresholds marked "illustrative" are starting floors to be tightened by the owned learning loops and per-tenant data — never loosened below the stated release floor without a recorded decision.*
