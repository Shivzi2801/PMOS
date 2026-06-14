# Retrieval Architecture

**Wave 0 · Slice 3 — future architecture, no implementation.**

Defines how the **READY** corpus is queried by future RAG, semantic search, and
agent workflows (M-13). Retrieval is **read-only with respect to processing** —
it never writes processing tables and never widens access. It reads
`document_chunks` (source of truth), the derived vector/lexical index (M-12), the
typed graph (hierarchy + provenance), and metadata. This file fixes the
architecture and the isolation guarantees; no orchestrator, fuser, or reranker
is built here.

---

## Retrieval pipeline (overview)

```
  query (+ TenantContext, +optional metadata filters)
        │
        ▼
  ┌───────────────────────────────────────────────────────────────┐
  │  PARALLEL LEGS (each tenant + workspace scoped from the start)   │
  │   ┌────────────┐  ┌─────────────┐  ┌──────────────┐             │
  │   │  Keyword    │  │  Semantic    │  │  Typed-graph  │            │
  │   │ (lexical/    │  │ (vector knn) │  │ (hierarchy +  │            │
  │   │  sparse BM25) │  │              │  │  provenance)  │            │
  │   └──────┬───────┘  └──────┬───────┘  └──────┬────────┘           │
  └──────────┼─────────────────┼─────────────────┼────────────────────┘
             ▼                 ▼                 ▼
        ┌─────────────────────────────────────────────┐
        │  ACL TRIM (PRE-FUSION)                         │  ◀── read_principals × session authority
        │  drop candidates the asker cannot see          │      BEFORE they influence ranking
        └───────────────────────┬─────────────────────────┘
                                ▼
        ┌─────────────────────────────────────────────┐
        │  FUSION (dense + sparse + graph signals)       │
        └───────────────────────┬─────────────────────────┘
                                ▼
        ┌─────────────────────────────────────────────┐
        │  RERANK (cross-encoder; per-tenant heads V2)   │
        └───────────────────────┬─────────────────────────┘
                                ▼
        ┌─────────────────────────────────────────────┐
        │  SELECT + honest abstention                    │  ◀── "n sources withheld by permissions"
        └───────────────────────┬─────────────────────────┘
                                ▼
                ranked, permission-safe results → RAG / agents / search UI
```

**ACL trim is pre-fusion, by design.** Permission filtering happens *before*
ranking so that a result the asker cannot see never influences scores or leaks
through ordering. This is non-negotiable and is the retrieval-side half of the
isolation model (the index-side half is the force-injected `organization_id`).

---

## Keyword retrieval

Exact-term and lexical matching for identifiers, codes, names, and precise
phrases that semantic search blurs (e.g. a Jira key, an error code, a product
SKU). Year-1 uses the vector store's native **sparse** representation (BM25-style
hybrid) so there is no separate lexical engine to operate. A dedicated lexical
engine (e.g. OpenSearch) is introduced **only** if exact-match/ID recall on the
largest tenant drops below the quality bar; the retrieval contract is
engine-agnostic so that swap is non-breaking.

- **Strengths:** precision on rare tokens, ids, exact phrases.
- **Inputs:** query terms; tenant + workspace scope; metadata filters.

## Semantic retrieval

Dense-vector k-NN over the embedding index for meaning-based matching ("find
content about checkout fraud" matches "payment abuse mitigation"). Vectors are
derived from chunks; the point id is the chunk id, so every semantic hit resolves
directly to a citable chunk and its provenance.

- **Strengths:** recall on paraphrase, concept, and cross-vocabulary matches.
- **Inputs:** query embedding (via Model Gateway, M-07); tenant force-injected
  `organization_id` filter; metadata filters; `read_principals` for trim.

## Hybrid retrieval

The default. Run keyword + semantic (+ typed-graph) **in parallel** and fuse.
Hybrid covers both precision (keyword) and recall (semantic), then rerank
sharpens ordering. Tiered for latency: coarse pre-filter → rescore →
cross-encoder rerank → select, so the hot path stays fast.

- **Typed-graph leg:** traverses the product hierarchy and provenance links to
  pull structurally related evidence (e.g. requirements under a feature) that
  pure similarity misses.

## Metadata filtering

Structured filters applied as **hard constraints** alongside the legs (not as
post-hoc filtering of ranked results, which would waste ranking budget and risk
leaks). Filterable fields come from `metadata-enrichment.md`: product, feature,
release, team, owner, tags, semantic categories, doc_type, time ranges.

- Filters are **always combined with** the mandatory tenant + workspace scope;
  they can narrow but never widen visibility.
- Filters are whitelisted and typed; no raw query fragments cross the boundary.

---

## Tenant isolation

The existential guarantee. Three independent layers must all fail for a leak
(carried from Slice 1's model):

1. **`organization_id` force-injected** into every retrieval filter (vector +
   lexical + graph), **from context, never from the request** — mirrors RLS.
2. **RLS on the Postgres source of truth** — any chunk read goes through the
   tenant-scoped policy.
3. **`read_principals` ACL trim pre-fusion** — within a tenant, the asker only
   sees sources their session authority permits.

A retrieval that cannot resolve a `TenantContext` is **refused**. Cross-tenant
over-return is a sev-0 class defect and is what the nightly cross-tenant probe
(Slice 1) exists to catch.

## Workspace isolation

Workspace is the **soft** boundary inside the hard org boundary (Slice 1).
Retrieval scopes to the active workspace(s) the session is authorized for. Unlike
the org boundary (RLS-enforced), workspace scoping is applied as an
**application-layer filter** in the retrieval legs (join-based workspace RLS
degrades plans — the deliberate defense-in-depth split). The effect is the same
to the caller: results never cross a workspace the asker lacks access to.

---

## Ranking strategy

Ranking is **multi-stage and tiered** to balance quality against the
`GET /search` latency budget:

1. **Coarse retrieval (per leg).** Each leg returns top-K candidates by its own
   cheap score (lexical score; vector distance; graph proximity). Tenant +
   workspace scope and metadata filters applied here.
2. **Pre-fusion ACL trim.** Remove candidates outside the asker's
   `read_principals` authority — before any score fusion.
3. **Fusion.** Combine leg signals into a unified candidate score (e.g. weighted
   rank fusion of dense + sparse + graph). Weights are configurable and
   measurable; they are part of the retrieval-quality eval surface.
4. **Rerank.** A cross-encoder reorders the fused top-N for relevance to the
   specific query. Per-tenant reranker heads are a V2 capability (base head
   Year-1); the base embedder is never per-tenant.
5. **Select + abstain honestly.** Return the top results with provenance; when
   evidence exists but is withheld by permissions, surface **"n sources withheld
   by permissions"** rather than silently omitting — honesty is a measured
   metric, never a silent drop.
6. **Cite by chunk id.** Every returned result resolves to a `chunk.id` →
   version → document chain, so RAG answers and agents can produce `Claim[]` with
   real citations and provenance.

**Ranking inputs that matter at scale:** recency/freshness signals, source
authority, metadata boosts (e.g. current release), and (future) per-tenant
learned reranking — all layered on top of the fused base score, never replacing
the isolation and ACL guarantees, which are applied first and are absolute.

---

## What retrieval never does

- Never writes processing tables or mutates the corpus.
- Never widens access beyond `read_principals` × session authority.
- Never returns a chunk whose source row is tombstoned/deleted (fail-closed via
  reconciliation).
- Never accepts `organization_id`/`workspace_id` from the request body.
- Never serves a half-migrated collection during a model swap (atomic pointer).

> No retrieval orchestrator, fuser, reranker, or search endpoint is implemented
> in this slice. This is the architecture M-13 builds against.
