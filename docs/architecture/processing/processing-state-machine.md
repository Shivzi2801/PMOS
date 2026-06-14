# Processing State Machine

**Wave 0 · Slice 3 — formal specification, no implementation.**

The authoritative finite-state machine governing document processing. The
**canonical state is owned by `document-service`** (Slice 2); processing stages
report transitions via the events in `processing-events.md`, and
`document-service` applies them through this machine. The machine is what makes
an at-least-once, retry-heavy, millions-of-documents pipeline **safe**: it
defines exactly which transitions are legal, which are rejected, how failures
recover, and which states are terminal.

States (from `document-lifecycle.md`): `Draft`, `Uploaded`, `Queued`,
`Processing`, `Chunked`, `Embedded`, `Indexed`, `Ready`, `Archived`, `Failed`,
`Deleted`.

---

## State diagram (happy path + branches)

```
        ┌────────┐
        │ Draft  │ (in-product authoring only)
        └───┬────┘
            │ commit first version
            ▼
        ┌──────────┐   commit version from ingestion/screening
        │ Uploaded │◄──────────────────────────────────────────────┐
        └───┬──────┘                                                 │
            │ DocumentQueued                                         │
            ▼                                                        │
        ┌────────┐                                                  │
   ┌───▶│ Queued │───────────────┐                                  │
   │    └───┬────┘   retry        │ poison/exhausted                │
   │        │ DocumentValidated   ▼                                  │
   │        ▼                ┌────────┐                              │
   │   ┌────────────┐        │ Failed │◄──────┐                      │
   │   │ Processing │───────▶│        │       │ (failure from        │
   │   └───┬────────┘  fail  └───┬────┘       │  any active stage)   │
   │       │ DocumentChunked     │ retry      │                      │
   │       ▼                     │ (re-queue) │                      │
   │   ┌─────────┐               └────────────┘                      │
   │   │ Chunked │──fail──────────────▲                              │
   │   └───┬─────┘                    │                              │
   │       │ DocumentEmbedded         │                              │
   │       ▼                          │                              │
   │   ┌──────────┐                   │                              │
   │   │ Embedded │──fail─────────────┤                              │
   │   └───┬──────┘                   │                              │
   │       │ DocumentIndexed          │                              │
   │       ▼                          │                              │
   │   ┌─────────┐                    │                              │
   │   │ Indexed │──fail──────────────┘                              │
   │   └───┬─────┘                                                   │
   │       │ DocumentReady (parity confirmed)                        │
   │       ▼                                                          │
   │   ┌────────┐   new version supersedes (new version re-enters) ──┘
   │   │ Ready  │──────────────────────────────────────────────────►
   │   └───┬────┘
   │       │ archive
   │       ▼
   │   ┌──────────┐  restore (re-index from chunks)
   └───│ Archived │──────────────► Ready
       └───┬──────┘
           │ delete
           ▼
       ┌─────────┐   purge (after retention window)
       │ Deleted │──────────────► [purged: truly terminal]
       └─────────┘
```

`Deleted` is reachable from **any** non-terminal state via a delete action
(omitted as individual arrows above to keep the diagram legible; enumerated in
the transition table).

---

## Valid transitions

| # | From | To | Trigger | Class |
|---|---|---|---|---|
| 1 | (none) | Draft | in-product create (no version) | normal |
| 2 | (none) | Uploaded | version committed from ingestion | normal |
| 3 | Draft | Uploaded | first immutable version committed | normal |
| 4 | Uploaded | Queued | `DocumentQueued` | normal |
| 5 | Queued | Processing | `DocumentValidated` | normal |
| 6 | Processing | Chunked | `DocumentChunked` | normal |
| 7 | Chunked | Embedded | `DocumentEmbedded` | normal |
| 8 | Embedded | Indexed | `DocumentIndexed` | normal |
| 9 | Indexed | Ready | `DocumentReady` (parity confirmed) | normal |
| 10 | Ready | Queued | new version supersedes (the **new** version enters at Queued) | normal (re-processing) |
| 11 | Ready | Archived | archive action / retention | normal |
| 12 | Ready | Deleted | delete action | normal |
| 13 | Archived | Ready | restore (re-index from chunks) | recovery |
| 14 | Archived | Deleted | delete action | normal |
| 15 | Queued | Failed | poison message / retries exhausted (`DocumentFailed`) | failure |
| 16 | Processing | Failed | validation/tenant/injection failure (`DocumentFailed`) | failure |
| 17 | Chunked | Failed | unchunkable/terminal (`DocumentFailed`) | failure |
| 18 | Embedded | Failed | embedding terminal (`DocumentFailed`) | failure |
| 19 | Indexed | Failed | index terminal (`DocumentFailed`) | failure |
| 20 | Failed | Queued | operator/auto retry (re-emit `DocumentQueued`) | retry/recovery |
| 21 | Failed | Deleted | give up / delete | normal |
| 22 | Draft | Deleted | abandoned-draft retention sweep | normal |
| 23 | any non-terminal | Deleted | delete action | normal |

---

## Invalid transitions (explicitly rejected)

The machine **rejects** these; an event attempting them is dropped with an
integrity log (and, where it indicates a defect, an alert). This is how
out-of-order or replayed events are absorbed safely.

| From | Attempted To | Why rejected |
|---|---|---|
| Uploaded | Chunked / Embedded / Indexed / Ready | Cannot skip Queued/Processing; no pipeline stage ran. |
| Queued | Embedded / Indexed / Ready | Cannot skip Processing + Chunked. |
| Processing | Embedded / Indexed / Ready | Cannot skip Chunked. |
| Chunked | Indexed / Ready | Cannot skip Embedded. |
| Embedded | Ready | Cannot skip Indexed (parity not yet confirmable). |
| Ready | Chunked / Embedded / Indexed | A Ready document does not move *backward*; a content change creates a **new version** that enters at Queued (transition #10), it does not regress the existing one. |
| Deleted (purged) | anything | Terminal; no transitions out of a purged document. |
| Archived | Chunked / Embedded / Indexed | Restore goes Archived → Ready (re-index), not into mid-pipeline states. |
| any | a non-adjacent forward state | Forward progress is strictly one stage at a time (except re-processing re-entry at Queued). |
| Failed | Chunked / Embedded / Indexed / Ready | Recovery re-enters at **Queued** (#20); it never jumps straight back into a mid/late stage. |

**Idempotent absorption.** Re-delivery of the event for a transition that has
already been applied (e.g. `DocumentChunked` arrives twice) is a **no-op**, not an
error — the document is already in (or past) the target state. The machine
distinguishes "already applied" (absorb) from "illegal jump" (reject).

---

## Recovery transitions

| From | To | Mechanism |
|---|---|---|
| Failed | Queued | Re-queue the version (operator action or auto-retry policy); pipeline restarts at the failed stage's beginning. Idempotency guarantees no duplication. |
| Archived | Ready | Restore: re-index from the immutable chunks (no re-chunk if content unchanged; vectors rebuilt from source of truth). |
| Indexed (drift) | Indexed/Embedded | Reconciler heals Postgres↔index drift in place; if unrepairable, re-queue the version for re-index rather than serving stale results. |
| Ready (post-hoc drift) | Queued | If the reconciler finds a Ready document's index is corrupt/missing, the version is re-queued; it stops contributing to retrieval until repaired (fail-closed). |

Recovery always re-enters through a **legal forward path**, never by forcing a
late state directly. This keeps the corpus consistent: a document is only Ready
when parity genuinely holds.

---

## Retry transitions

Retries operate **within** an active stage before any state change, and **across**
states only via `Failed → Queued`.

- **In-stage retry (no state change).** A transient error (provider 5xx,
  timeout, 429, lock contention) is retried with **exponential backoff + jitter**,
  bounded per lane (live fails faster to protect freshness; bulk tolerates more).
  The document stays in its current active state during in-stage retries.
- **Cross-state retry (`Failed → Queued`).** When in-stage retries are exhausted,
  the document moves to **Failed** and is dead-lettered. A retry policy or
  operator re-queues it (transition #20), re-running from the failed stage.
- **Idempotency makes retries safe.** Source dedupe (`external_event_id`),
  content dedupe (`content_hash`), deterministic chunk identity, and
  event-consumer dedupe (`event_id`) together guarantee that any retry or replay
  produces **exactly-once effect** (see `ingestion-pipeline.md` §Idempotency).
- **Kill-switch interaction.** While a tenant/connector kill-switch is engaged,
  retries pause; the document parks in its current state and resumes when cleared.

---

## Terminal states

| State | Terminal? | Notes |
|---|---|---|
| **Ready** | terminal *success* (steady state) | Stable end of processing; only leaves via re-processing (new version → Queued), Archive, or Delete. It is the working "done" state. |
| **Failed** | **not** terminal | Recoverable holding state: `Failed → Queued` (retry) or `Failed → Deleted` (give up). |
| **Archived** | semi-terminal | Retained, excluded from retrieval; reversible via restore (`Archived → Ready`) or `Archived → Deleted`. |
| **Deleted (soft)** | not yet terminal | Soft-deleted; tombstoned downstream; reversible only by the operator within the retention window before purge. |
| **Deleted (purged)** | **truly terminal** | After the 30-day retention window, content + chunks + vectors are hard-purged, leaving only audit tombstones. No transitions out. |

**Design intent.** There is exactly **one** truly terminal sink (purged
`Deleted`) and one **steady-state success** (`Ready`). Every failure is
recoverable until an explicit decision to delete. This is the property an
enterprise, multi-tenant, millions-of-documents pipeline requires: nothing is
ever silently lost, every document has a defined position, and every anomalous
event is either safely absorbed (idempotent re-delivery) or safely rejected
(illegal jump) — never applied incorrectly.

---

## Invariants the machine guarantees

1. **Retrievable ⇒ Ready.** A document influences retrieval **only** in Ready.
2. **Forward-by-one.** Normal progress advances exactly one stage at a time;
   the only multi-step "jump" is re-processing re-entry at Queued (a new version).
3. **No backward regression of a version.** Content changes create new versions;
   an existing version never moves backward in state.
4. **Fail-closed.** A document whose derived index is missing/corrupt does not
   serve stale results; it is re-queued or withheld.
5. **Tenant isolation across all states.** RLS + force-injected `organization_id`
   + `read_principals` hold in every state, including Failed/Archived/Deleted —
   no state is a cross-tenant escape hatch.
6. **Every transition is an event + a trace.** Each transition corresponds to a
   processing event carrying the document's `correlation_id`, so the full
   journey — including failures and retries — is one reconstructable trace.
