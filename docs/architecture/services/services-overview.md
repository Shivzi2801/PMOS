# PMOS · Backend Services Architecture

**Wave 0 · Slice 2 — Service Boundaries & Contracts**

> **Scope of this slice.** This slice defines *architecture and contracts only*.
> It contains **no** REST/GraphQL endpoints, no authentication, no embedding
> generation, no vector search, no queue implementation, and no business logic.
> Every README here is an **implementation blueprint**: it fixes the service
> boundary, the data it owns, the events it speaks, and the contracts it must
> honor — so that later slices can build the internals without renegotiating
> seams. Anything labelled *Future API* is documentation of intent, not an
> instruction to build now.

This document is authoritative for **how PMOS backend services relate to one
another**. Individual service READMEs are authoritative for **what each service
does internally**. The `contracts/` directory is authoritative for the **wire
shapes** that cross service boundaries.

---

## 1. Overall service architecture

PMOS Year-1 is a **modular monolith deployed as cooperating services behind a
single client surface (the BFF, M-05)**. The decomposition is by *domain
ownership*, not by technical layer. Three services are introduced in this
slice; they sit on the front half of the Knowledge Platform pipeline and on the
system-of-record established in Slice 1.

```
                         ┌──────────────────────────────────────────────┐
   external sources ───► │  ingestion-service        (maps to M-08)       │
   (Zendesk, Jira,       │  - receive / normalize ingress                 │
    Notion, Slack, ...)  │  - lane routing, retries, source ACL capture   │
                         └───────────────┬────────────────────────────────┘
                                         │  content.ingested (events)
                                         ▼
                         ┌──────────────────────────────────────────────┐
                         │  document-service         (maps to M-03)       │
                         │  - system of record for documents +            │
                         │    immutable versions                          │
                         │  - owns the product hierarchy rows             │
                         └───────────────┬────────────────────────────────┘
                                         │  document.version_created
                                         ▼
                         ┌──────────────────────────────────────────────┐
                         │  chunking-service         (maps to M-03        │
                         │  - deterministic version → chunks               │
                         │  - owns document_chunks lifecycle              │   chunker)
                         │  - emits document.chunked (hand-off to index)  │
                         └───────────────┬────────────────────────────────┘
                                         │  document.chunked
                                         ▼
                            (future) index fan-out / embedding   ── M-12, later slice
```

**Pipeline position.** The full Knowledge-Platform pipeline is strictly
sequential: **ingest → screen → extract → resolve → index → retrieve**
(M-08 → M-09 → M-10 → M-11 → M-12 → M-13). This slice scaffolds the three
services that own the *front* of that pipeline plus the system of record:
`ingestion-service` (M-08), `document-service` (M-03 documents + hierarchy), and
`chunking-service` (the M-03 chunker that produces the M-12 input). Screening
(M-09), extraction (M-10), entity resolution (M-11), and index fan-out (M-12)
are downstream consumers of the events defined here and are out of scope.

**Communication substrate.** Services never call each other synchronously for
state changes. They communicate through the **transactional outbox + event
backbone (M-04)**: a state change and the event announcing it are committed in
the *same database transaction*, then relayed to subscribers. The event
envelope and ordering guarantees are owned by M-04 and documented in
`contracts/service-interfaces.md`. Synchronous reads (later, via the BFF) are
the only cross-boundary synchronous interactions, and even those go through the
owning service, never directly into another service's tables.

**Deployment reality (Year-1 vs Year-2).** These boundaries are drawn so the
Year-1 modular monolith can be split into independently deployed services (and,
at cell scale, replicated per cell) with **no contract change** — the seam is
the event envelope and the data-ownership rule, both of which already hold
inside the monolith. `organization_id` on every row and event (Slice 1) is what
makes the eventual split a routing change, not a rewrite (TD-1).

---

## 2. Service ownership model

Every service has **exactly one owner team** and owns a **disjoint set of
database tables**. No table is written by more than one service. A service is
the *only* writer of its tables and the *only* publisher of its domain events.

| Service | Maps to module | Owns (tables) | Owns (events, prefix) |
|---|---|---|---|
| `ingestion-service` | M-08 | `connectors`, `connector_health`, `connector_coverage`, `ingestion_events`, `webhook_dedupe` | `connector.*`, `content.ingested`, `backfill.*`, `ingestion.*` |
| `document-service` | M-03 (documents + hierarchy) | `documents`, `document_versions`, plus the hierarchy (`products`…`requirements`) and `workspaces` | `document.*`, `product.*`, `feature.*`, `epic.*`, `user_story.*`, `requirement.*`, `workspace.*` |
| `chunking-service` | M-03 (chunker) | `document_chunks` (write-owner) | `document.chunked`, `document.processing_*` |

> **Boundary note on `document_chunks`.** In the M-03 module spec the chunker is
> an internal worker of Core Persistence. In this slice we promote chunking to
> its own service boundary because its lifecycle, throughput profile
> (10⁸ chunks per large tenant), and future embedding hand-off differ sharply
> from the transactional hierarchy CRUD. `chunking-service` is the **sole
> writer** of `document_chunks`; `document-service` owns the documents and
> versions that are its input. This keeps the partitioned, high-write table
> behind a single owner — the data-ownership rule below.

**Ownership of shared columns.** `organization_id` and `workspace_id` are
present on every row (Slice 1) but are *stamped from the tenant context*, never
chosen by a calling service. The RLS policy (Slice 1) enforces this regardless
of which service writes.

---

## 3. Service interaction model

There are exactly **two** legal interaction modes between services:

**(A) Asynchronous, event-driven (the default, for all state changes).**
A service commits its state change and an outbox row in one transaction. The
M-04 relay publishes the event. Subscribing services react. The producer does
**not** know or care who consumes; consumers do **not** call back into the
producer. This is how `ingestion-service` hands off to `document-service`, and
how `document-service` hands off to `chunking-service`.

**(B) Synchronous read, owner-mediated (later slices, via the BFF).**
When a client needs data, the BFF (M-05) calls the **owning** service's read
path. One service never reads another service's tables directly — it asks the
owner. In this slice no synchronous endpoints exist; the *future* read surfaces
are documented per service so the seam is reserved.

**Forbidden interactions** (enforced by review and, later, by module-boundary
lint):
- No service writes another service's tables.
- No service emits another service's domain events.
- No synchronous service-to-service call to *mutate* state (mutations are
  always the owner reacting to an event or a command routed through the BFF).
- No shared mutable in-process state across service boundaries.

Every event and every (future) request carries a **correlation id** and a
**causation id** so a single ingestion can be traced end-to-end across all
three services (see `contracts/service-interfaces.md`).

---

## 4. Event-driven design principles

1. **Outbox or it didn't happen.** No event is ever published without its state
   change committed in the same transaction (the M-04 outbox invariant). There
   is no `publish()` call separate from the database write.
2. **Events are facts, in the past tense.** `DocumentCreated`, not
   `CreateDocument`. An event records something that *has happened* and is
   immutable once emitted.
3. **Tenant-stamped.** Every event payload carries `organization_id`. Consumers
   **must** filter on it; a consumer that fans an event into a shared projection
   without tenant filtering is a cross-tenant defect (same rule as `BYPASSRLS`
   workers in Slice 1).
4. **Idempotent consumers.** The transport is at-least-once. Every consumer must
   tolerate redelivery (dedupe on `event_id`, or make the projection write
   naturally idempotent). Producers dedupe ingress on `external_event_id`.
5. **Schema-versioned, additive-first.** Event payloads carry a `schema_version`.
   Changes are additive within a major version; a breaking change is a new event
   type or a new major version, never a silent reshape (mirrors the BFF's
   `PMOS-Version` discipline).
6. **Ordering is per-aggregate, not global.** Consumers may assume ordering of
   events for a single `aggregate_id`, not across aggregates. Cross-aggregate
   causality is expressed via `causation_id`.
7. **Lane discipline survives in events.** Ingestion events carry the priority
   `lane` (live ≤2 min / standard ≤15 min / bulk best-effort) so downstream
   processing preserves the guarantee that backfills never delay live detection.

The canonical envelope, delivery semantics, and replay model live in
`contracts/service-interfaces.md` and are owned by M-04.

---

## 5. Dependency rules

Dependencies point **inward toward the system of record and the platform
substrate**, never outward toward consumers.

```
   platform substrate (Slice 1: tenancy, RLS, schema, UUIDv7)   ← depended on by all
        ▲
        │
   document-service (system of record)
        ▲                 ▲
        │                 │
   chunking-service   ingestion-service
```

- **Every service depends on the Slice-1 substrate** (tenancy context, RLS,
  conventions). None of them re-implements isolation; they inherit it.
- **`ingestion-service` depends on `document-service`** only through events
  (it announces ingested content; `document-service` decides what to persist).
  It does **not** depend on `chunking-service` at all.
- **`chunking-service` depends on `document-service`** through events
  (it reacts to `document.version_created`). It does **not** depend on
  `ingestion-service`.
- **No cyclic dependencies.** If two services appear to need each other, the
  shared concept belongs in a third owner or in an event contract.
- **No service depends on a downstream consumer** (screening, extraction,
  indexing). The pipeline is a one-way fan-out; producers are oblivious to
  consumers.
- **The event contracts in `contracts/` are a dependency of everyone and depend
  on no service.** They are the stable center.

---

## 6. Data ownership rules

1. **One writer per table.** The owning service is the sole writer. Others read
   only via the owner (later) or via events. Slice 1's table list is partitioned
   across the three services in §2 with no overlap.
2. **The system of record is Postgres; everything else is derived.** Chunks are
   the source of truth for retrieval; vectors (future, M-12) are *derived* from
   chunks and always rebuildable. No service may treat a derived store as
   authoritative.
3. **`document_chunks.id` is the contract.** It *is* the future Qdrant point id
   (Slice 1). `chunking-service` mints it; downstream indexing consumes it
   verbatim; no mapping table ever exists.
4. **`content_hash` governs idempotent re-processing.** A re-ingested or
   re-chunked identical body must not produce duplicate downstream work; the
   hash is the dedupe key end-to-end.
5. **`read_principals` travels with the chunk.** Source ACLs captured at ingress
   (M-08) are carried onto each chunk so downstream retrieval can trim
   *pre-fusion*. Ownership of the *value* is the chunk's; ownership of the
   *capture* is ingestion's.
6. **Soft-delete is the erasure path.** Deletes set `deleted_at` (Slice 1);
   hard purge is a separate, audited 30-day process. A `DocumentDeleted` event
   triggers downstream tombstoning, not silent disappearance.
7. **Tenant columns are stamped, never accepted.** `organization_id` /
   `workspace_id` come from the tenant context, never from a request body or a
   peer service's payload field; RLS enforces it at the row level.

---

## Directory map

```
backend/services/
├── README.md                      ← this file (architecture, authoritative for seams)
├── document-service/README.md     ← M-03 documents + hierarchy blueprint
├── ingestion-service/README.md    ← M-08 ingress blueprint
├── chunking-service/README.md     ← M-03 chunker blueprint
└── contracts/
    ├── document-events.md         ← DocumentCreated/Updated/Deleted/ProcessingStarted/Completed
    ├── ingestion-events.md        ← IngestionRequested/Started/Completed/Failed
    └── service-interfaces.md      ← envelope, request/response/error contracts, idempotency, correlation, tracing
```

---

*Slice 2 is contracts and boundaries only. No runtime, no endpoints, no logic.
Later slices implement service internals against these fixed seams.*
