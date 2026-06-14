# PMOS — Frontend Modules

### Implementation-Ready Frontend Module Breakdown

**Source of truth:** `PMOS_MASTER_SPEC_Final.md` (Constitution v1.0), `Feature_Inventory.md`, `User_Flows.md`, `API_Design.md`, `API_Inventory.md`.
**Scope:** Every Foundation, MVP, and V1 feature (F-01 … F-42). V2/Future features (F-43 … F-61) are named only where a Year-1 module must leave a seam for them.
**Stack assumed (per spec §13):** Next.js (App Router, React Server Components, Vercel) · TypeScript · the BFF three-protocol contract (REST `/api/v1`, GraphQL `/api/graphql`, SSE) · a built-in dev fixture implementing the exact `/api/v1` contract so the frontend builds before the real BFF exists.

**How to read this document.** Part 1 defines the cross-cutting architecture every module depends on (component library, global state, auth, navigation, routing, dependency graph). Part 2 defines the modules, grouped into the six product areas. Part 3 is the recommended build sequence from module 1 to the final module. Each module is written so a frontend engineer can scaffold it immediately: the screens, components, hooks, state slices, and API calls are concrete, not aspirational.

**Two invariants that shape every module.** (1) *The system speaks first* — no surface opens with an empty input or a grid of widgets; every screen opens with a finding. (2) *`Claim[]` is the protocol* — every AI-generated prose field arrives as `{text, citations[], kind, confidence}` and is rendered with the Provenance Underline, never as a plain string. These two rules are baked into the shared component library so individual modules inherit them for free.

---

# Part 1 — Cross-Cutting Frontend Architecture

## 1. Frontend Dependency Graph

Arrows read **A → B = "module A must exist (at least in scaffold) before module B can be completed."** Grouped by the wave structure inherited from the Feature Inventory. The frontend dependency graph is *shorter* than the backend one because the dev fixture (the BFF contract implemented locally) lets most UI modules start against a stable contract before their backend is live.

```
WAVE 0 — FRONTEND SUBSTRATE (depends only on the API contract + dev fixture)
────────────────────────────────────────────────────────────────────────────
  M0 App Shell & Routing ─┬─→ M1 Design System (Meridian)
                          ├─→ M2 API/Data Layer (REST+GraphQL+SSE clients)
                          └─→ M3 Auth & Session
        M1,M2 ─→ M4 Shared Component Library (Claim/Provenance, Tide host, Line host)
        M2    ─→ M5 Global State Architecture (stores + cache)

WAVE 1 — CORE NAVIGATION & READ SURFACES (needs M0–M5)
────────────────────────────────────────────────────────────────────────────
  M4,M5 ─→ M6 The Line (Go/Ask/Do command interface)
  M4,M5 ─→ M7 The Brief (system-speaks-first home)
  M4    ─→ M8 The Meridian Canvas & Altitudes
  M8    ─→ M9 Streams, Lenses & Containers
  M4    ─→ M10 The Tide & Meridian Bar
  M6,M7 ─→ M11 Provenance Lens (overlay; consumed everywhere)

WAVE 2 — KNOWLEDGE PLATFORM (needs M6, M11, data layer)
────────────────────────────────────────────────────────────────────────────
  M6  ─→ M12 Search (Line "Go")
  M6,M11 ─→ M13 Ask-the-Brain (Line "Ask", SSE)
  M2  ─→ M14 Connectors & Ingestion Admin
  M11 ─→ M15 Feedback Intelligence (the wedge)
  M15 ─→ M16 The Free Diagnostic (GTM)

WAVE 3 — AI WORKSPACE (needs M2 run client, M11, M5 run store)
────────────────────────────────────────────────────────────────────────────
  M2,M5 ─→ M17 Agent Run Console (run lifecycle, SSE progress, approvals)
  M17,M11 ─→ M18 PRD Studio (PRD agent, L1/L2)
  M18    ─→ M19 Story Studio (Story agent, L1/L2)
  M17    ─→ M20 Conductor & Research surfaces
  M19    ─→ M21 Living Sync (one-way, diff+rationale, L2 approval)

WAVE 4 — DECISIONS, PRODUCT INTELLIGENCE, ROADMAP & PLANNING
────────────────────────────────────────────────────────────────────────────
  M11,M17 ─→ M22 Decision Sheet & Commit Ceremony
  M22     ─→ M23 Decision Ledger (read surfaces)
  M11,M15 ─→ M24 Prioritization (The Arena)
  M8,M17  ─→ M25 Roadmap (Horizon)

WAVE 5 — ADMINISTRATION (cross-cutting; can start in parallel from Wave 0)
────────────────────────────────────────────────────────────────────────────
  M3   ─→ M26 Org/Workspace/Team Admin
  M2,M5 ─→ M27 Billing & Consumption Metering
  M3   ─→ M28 Compliance & Data Requests (DSAR)
  M3   ─→ M29 Operations & Kill Switches
```

**The two longest frontend poles.** (1) *The AI Workspace chain* (M17 → M18 → M19 → M21): each surface consumes the run lifecycle, approval endpoints, and `Claim[]` rendering of its predecessor, and the PRD/Story studios are where the engineer-quality bar (edit-distance) is felt. (2) *The Design System + Canvas pair* (M1 + M8): the largest pure-frontend track, started in Wave 0 and never blocking on the backend pipeline. Start both early.

---

## 2. Shared Component Library

The shared library is module **M4** in the build sequence, but it is documented first because every other module imports from it. It is organized in three tiers: **design-system primitives** (atoms, owned by M1), **PMOS-semantic components** (the components that encode the two invariants), and **layout/host components** (the persistent chrome).

### 2.1 Design-system primitives (tier 1 — owned by M1 Meridian Design System)

These are theme-aware, accessibility-correct atoms with no PMOS domain knowledge.

- `Button`, `IconButton`, `Toggle`, `SegmentedControl` — physics-based press feedback (one spring curve, `stiffness 320 / damping 32`).
- `TextField`, `TextArea`, `ComboBox`, `Select` — used sparingly; the IA prefers the Line over forms.
- `Surface`, `Card`, `Panel`, `Sheet`, `Overlay`, `Drawer` — the container atoms; `Sheet` and `Overlay` host the Decision Sheet and Provenance Lens.
- `Text`, `Heading`, `Mono`, `SerifBlock` — role-correct typography wrappers (sans chrome / **serif for artifacts & evidence** / mono for IDs/hashes/metric deltas). 15px base, 1.2 modular scale, nothing below 12px.
- `SemanticHue` tokens — exactly five, used only when they carry meaning: Verdant (verified/kept/earned), Signal Blue (live AI/citations), Amber (pending/expiring/drift), Vermilion (contradiction/broken/risk), Violet (simulation/hypothetical — *anything not yet real is violet*). The chrome is monochrome.
- `AtmosphereProvider` — switches Daylight ↔ Midnight by **content mode** (not OS theme), 600ms crossfade.
- `Motion` / `Spring` wrappers — single spring curve; honors `prefers-reduced-motion` (replaces travel with crossfade); 350ms motion budget per interaction; AI "thinking" rendered as a luminous pulse, never a spinner.
- `LoadingPulse`, `FreshnessBadge`, `QualityBadge` — honest-degradation primitives: freshness on every read surface, quality badge on degraded-model answers.
- `Spinner` is deliberately **absent** — its omission is enforced in lint.

### 2.2 PMOS-semantic components (tier 2 — owned by M4, encode the invariants)

These are the components that make the spec's invariants structural rather than per-screen discipline.

- **`Claim`** — renders a single `{text, citations[], kind, confidence}`. Applies the **Provenance Underline**: thickness encodes evidence weight with redundant non-color glyph encoding (`.prov-single` 1px / `.prov-corroborated` 2px / `.prov-inference` dotted / `.prov-simulated` violet / `.prov-degraded`). `kind: "inference"` renders visually distinct. Clicking opens the Provenance Lens (M11).
- **`ClaimList`** — renders a `Claim[]` as flowing prose (the artifact-reading experience), serif body, citations inline. This is the single component every AI prose field passes through; no module ever renders AI text as a raw string.
- **`ProvenanceLensTrigger`** / **`ProvenanceLens`** — the openable evidence overlay; must resolve in <400ms (enforced as a perf budget). Shows source chunks, `read_principals`-trimmed, with "n sources withheld by permissions" when applicable.
- **`MetricValue`** — renders any number; refuses to render free-text numbers, binds to a governed metric-store value, shows the join/source on hover. "Numbers are tools, not text" enforced at the component boundary.
- **`AutonomyBadge`** — shows the current Trust-Ladder level (L0–L2 in Year-1) of any agent-produced artifact, plus `ai_generated` provenance and `source_run_id`.
- **`ApprovalGate`** — the standard L2 human-checkpoint control (Approve / Edit / Decline) used by PRD, Story, and Sync studios; logs edit-distance on edit.
- **`FindingCard`** — the "system speaks first" unit: what changed · what it means · what's recommended, every line a `Claim`. The Brief and the Tide are both compositions of `FindingCard`s.
- **`EmptyState`** — there is no blank-input empty state; the empty state is always a finding or an honest "retrieval failed — try Ask."

### 2.3 Layout / host components (tier 3 — the persistent chrome, owned by M0 + M10)

- **`AppShell`** — the persistent canvas + chrome wrapper; hosts the Meridian Bar, the Tide, and the Line overlay so they are reachable from every route.
- **`LineHost`** — the `⌘K` overlay mount point (M6).
- **`TideRail`** — the persistent ranked-notification strip (M10).
- **`MeridianBar`** — bottom strip: waypoints `⌘1–5`, time scrubber, altitude control (M10).
- **`AltitudeProvider`** — the Org / Stream / Object altitude context consumed by the canvas and lenses (M8).

---

## 3. Global State Architecture

PMOS is read-mostly (≈92% of users are viewers hitting precomputed projections), streaming-heavy (SSE for Ask, runs, the Tide), and command-oriented (REST mutations). The state architecture matches that shape: **a server-cache layer for reads, lightweight client stores for ephemeral UI/session state, and a streaming layer for SSE.** No single monolithic store.

### 3.1 Layers

1. **Server-state cache (TanStack Query, or equivalent).** Owns everything fetched from the BFF — GraphQL lenses (Brief, Canvas, Decision Sheet, Horizon, Arena), REST reads, job/run status. Cache keys are tenant-scoped (`[orgId, workspaceId, …]`) so a workspace switch never bleeds. Freshness metadata from the server populates the `FreshnessBadge`; staleness is rendered, never hidden (honest degradation).
2. **Session & identity store (small, global).** Current user, Clerk session claims, resolved authority (RBAC × ABAC), `TenantContext` (`organizationId`, `workspaceId`), active atmosphere (Daylight/Midnight), and the current altitude. Read by routing guards and the API layer (every request injects tenant context).
3. **UI/ephemeral store (per-surface, lightweight — Zustand-style slices).** The Line's open/mode state, overlay/sheet stack, canvas pan/zoom + selection, current Stream/Lens, draft edits awaiting approval. Not persisted to the server; reconstructed on reload.
4. **Streaming layer (SSE subscription manager).** A single connection manager that owns Ask streams, run-progress streams, job-progress streams, and the Tide stream. Handles `Last-Event-ID` resume, 15s heartbeats, 15-min server termination + reconnect, and stream-ticket minting for `EventSource` cases. Streamed events are reduced into the appropriate store (e.g. Ask claims accumulate into the Ask slice; run steps into the run store).

### 3.2 State slices (the canonical client slices)

- `sessionSlice` — identity, claims, authority, tenant context, atmosphere, default altitude.
- `lineSlice` — open state, current input, detected mode (Go/Ask/Do), recent commands.
- `navigationSlice` — current waypoint, altitude, Stream/Lens, time-scrubber position.
- `overlaySlice` — the modal/sheet/lens stack (Provenance Lens, Decision Sheet, approval prompts).
- `runSlice` — active agent runs keyed by `runId`, their step traces and progress, parent/child relationships (Conductor).
- `askSlice` — in-flight Ask sessions, accumulated `Claim[]`, abstention state, quality badge.
- `tideSlice` — ranked Tide items, interruption gating (Vermilion only), unread state.
- `canvasSlice` — pan/zoom, viewport, selection, altitude transitions.
- `draftSlice` — un-approved artifact edits (PRD/Story/Sync), with baseline for edit-distance.

**Rule:** server data lives in the query cache, *never* duplicated into client slices. Slices hold only what the server doesn't own (ephemeral UI, in-flight streams, un-committed drafts).

---

## 4. Authentication Flow Architecture

Identity is **Clerk** (SSO SAML/OIDC, MFA for editor+ roles, SCIM for enterprise, JWKS-verified JWTs). **Passwords never touch PMOS.** The frontend never holds a credential; it holds a Clerk session and exchanges it for BFF authorization.

### 4.1 Flow

1. **Unauthenticated** → route guard redirects to the **Login** surface → Clerk-hosted SSO. The frontend renders only the "Sign in with your company SSO" entry; the IdP handshake is external.
2. **IdP redirect** → (MFA challenge if the role is editor+) → return with a Clerk session.
3. **Session exchange** → the frontend calls `GET /api/v1/session`; the BFF verifies the JWT via JWKS and returns resolved authority (RBAC × ABAC × source-ACL trim) and the bound `TenantContext`. This populates `sessionSlice`.
4. **Landing** → the user lands on **the Brief** at their default altitude — never a blank dashboard.
5. **SCIM deprovision** → a deprovisioned user is blocked at the session exchange (`401`); the UI shows an honest "access has been removed" state, not a login loop.

### 4.2 Frontend responsibilities & guards

- **`<RequireAuth>`** route wrapper — gates every authenticated route; redirects to Login when `sessionSlice` is empty or the session is expired.
- **`<RequireRole role="editor">`** — gates editor+ surfaces (commit, approve, connector admin, kill switches). Viewers (the unlimited free tier) get read surfaces only; write controls are absent, not merely disabled, where the role can never act.
- **Authority is never trusted client-side for enforcement** — guards are a UX convenience; the BFF re-checks every command. The two-principals model means an *agent's* authority (capability token) is entirely server-side and never represented in the client beyond the `AutonomyBadge`.
- **Stream-ticket minting** — for any `EventSource`-based SSE, the streaming layer calls `POST /api/v1/stream-tickets` for a single-use 60s ticket; the session token is never placed in a URL.
- **Re-auth on expiry** — a 401 from the BFF triggers a silent Clerk refresh; on failure, a non-destructive re-auth prompt preserves in-flight drafts (`draftSlice`).

---

## 5. Navigation Architecture

PMOS deliberately has **no folders, no page tree, no global projects list, no dashboard-of-widgets.** Navigation is "one surface, many lenses." The navigation model has four mechanisms, all keyboard-first (100% pointer-free doctrine):

1. **The Line (`⌘K`)** — the primary navigator. *Go* mode is navigation/search (<50ms); it is how users reach any object or surface. If a user wants to "organize," they ask the Line — retrieval is the organizing mechanism.
2. **The Meridian Bar** — the persistent bottom strip: **waypoints `⌘1–5`** (the five top-level surfaces), a **time scrubber** (replay org state at a past moment), and an **altitude control** (Org 30k ft / Stream 3k ft / Object ground).
3. **Altitude** — vertical navigation. The same canvas zooms from the CPO's resting Org view down to a single Object (a Sheet). Altitude is a first-class navigation axis, not a separate screen set.
4. **The Tide** — event-driven navigation: ranked findings are entry points into the objects they concern; a Vermilion item is a direct link to the contradiction/risk it represents.

**Waypoint assignment (`⌘1–5`)** — the five waypoints map to: `⌘1` Brief · `⌘2` Canvas (current Stream) · `⌘3` Arena/Prioritization · `⌘4` Horizon/Roadmap · `⌘5` Tide. The Line (`⌘K`) and altitude (`⌘1–5` modified) are orthogonal to the waypoints.

**Keyboard doctrine** — every navigable element is reachable by keyboard; the full keyboard map *is* the screen-reader spine (WCAG 2.2 AA in both atmospheres). No interaction requires a pointer.

---

## 6. Routing Architecture

Next.js App Router. Because the IA is "one canvas, many lenses," routing is **lens-over-canvas**, not a deep page hierarchy. Most "screens" are lens states of a persistent shell, expressed as parallel/intercepting routes and search params rather than full page navigations (which preserves canvas state and the 60fps pan/zoom budget).

### 6.1 Route map

```
/(auth)
  /login                         → Login (Clerk SSO entry)
  /sso-callback                  → IdP return + session exchange
/(app)                           → AppShell (persistent: canvas, Meridian Bar, Tide, Line host)
  /                              → The Brief (default landing)
  /canvas                        → Meridian Canvas (altitude via ?alt=org|stream|object)
    /canvas/stream/[streamId]    → a Stream at 3k ft
    /canvas/object/[type]/[id]   → a single Object at ground (Decision Sheet, PRD, Story…)
  /arena                         → Prioritization (The Arena)
  /horizon                       → Roadmap
  /feedback                      → Feedback Intelligence lens
  /runs/[runId]                  → Agent Run Console (also openable as overlay)
  /admin
    /admin/connectors            → Connectors & Ingestion
    /admin/billing               → Billing & Consumption
    /admin/members               → Org/Workspace/Team admin
    /admin/compliance            → Compliance & DSAR
    /admin/operations            → Operations & Kill Switches
  /diagnostic                    → Free Diagnostic (also reachable pre-auth via marketing)
@overlay (parallel route slot)
  /provenance/[claimId]          → Provenance Lens (intercepting; opens over any route)
  /decision/[id]                 → Decision Sheet (intercepting; opens over canvas)
  /ask                           → Ask streaming surface (intercepting; opens from the Line)
```

### 6.2 Routing principles

- **Parallel `@overlay` slot** hosts the Provenance Lens, Decision Sheet, and Ask surface so they open *over* the current canvas without tearing it down — preserving pan/zoom, altitude, and selection.
- **Intercepting routes** give overlays real URLs (shareable, deep-linkable, back-button-correct) while rendering as overlays in-context.
- **Altitude and Stream/Lens are search params** on `/canvas`, not separate route trees, so altitude transitions are state changes (animated, ≥60fps) rather than navigations.
- **Server Components** render the read-mostly lens shells (Brief, Canvas, Horizon) from GraphQL persisted queries; **Client Components** own anything streaming or interactive (the Line, Ask, run progress, the Tide, canvas interaction).
- **Tenant context** is resolved in middleware on every `(app)` route; a route that cannot resolve a `TenantContext` never renders (mirrors the data layer's refusal).
- **Time scrubber** is a search param (`?t=<iso>`) on read surfaces; setting it re-renders lenses against historical projections.

---

# Part 2 — Frontend Modules

Each module below carries the full implementation header: **Module Name · Purpose · Screens · Components · Hooks · State Management · APIs Consumed · Dependencies · Reusable UI Components · Design System Components Used · Complexity · Recommended Build Order.** "Recommended Build Order" is the module's position (M-number) in the global sequence given in Part 3.

Modules are grouped into the six product areas requested: **Core Platform · Knowledge Platform · AI Workspace · Product Intelligence · Roadmap & Planning · Administration.** The Wave-0 substrate modules (M0–M5) and the navigation/read surfaces (M6–M11) belong to **Core Platform**; everything else falls into its named group.

---

# Group A — Core Platform

*The substrate every other group sits on: app shell, design system, data layer, auth, the shared library, global state, and the core navigation/read surfaces (Line, Brief, Canvas, Streams, Tide, Provenance Lens). Maps to Foundation features F-01, F-02, F-05, F-14 (frontend portions) and V1 surface features F-33, F-32, F-35, F-36, F-37, F-38.*

---

## M0 · App Shell & Routing Foundation

- **Module Name:** `app-shell`
- **Purpose:** The persistent Next.js App Router shell — the canvas + chrome wrapper that hosts the Meridian Bar, the Tide rail, and the Line overlay so they are reachable from every authenticated route. Establishes the lens-over-canvas routing model, the `@overlay` parallel slot, intercepting routes, and tenant-context middleware. Nothing renders without it.
- **Screens Included:** None of its own; it is the frame. Provides the mount slots for every other surface.
- **Components Included:** `AppShell`, `RouteLayout`, `OverlaySlot` (the `@overlay` parallel route host), `TenantContextBoundary`, `AltitudePreservingTransition`.
- **Hooks Required:** `useTenantContext()`, `useRouteAltitude()`, `useOverlayStack()`, `useWaypoint()`.
- **State Management:** `navigationSlice` (waypoint, altitude, time-scrubber position), `overlaySlice` (the lens/sheet stack). Reads `sessionSlice` for tenant context.
- **APIs Consumed:** None directly (shell). Relies on middleware that resolves `TenantContext` from the Clerk session.
- **Dependencies:** None (root of the frontend graph). Requires the dev fixture for the contract.
- **Reusable UI Components:** `Surface`, `Overlay`, `Drawer`.
- **Design System Components Used:** `AtmosphereProvider`, `Motion`/`Spring`, role-correct typography wrappers.
- **Complexity:** Medium
- **Recommended Build Order:** **M0** (first).

---

## M1 · Meridian Design System

- **Module Name:** `design-system`
- **Purpose:** The full Meridian design system (F-38): two atmospheres (Daylight/Midnight, 600ms crossfade, switched by content mode), exactly five semantic hues used only when meaningful, role-correct typography (sans chrome / serif artifacts / mono IDs), physics-based motion (one spring curve, luminous-pulse "thinking," 350ms budget, `prefers-reduced-motion`), and the design tokens every component consumes. WCAG 2.2 AA in both atmospheres; per-reader i18n from one graph. This is the largest pure-frontend track and starts in Wave 0.
- **Screens Included:** A living component gallery / Storybook (internal), not an end-user screen.
- **Components Included:** All tier-1 primitives (§2.1): `Button`, `Toggle`, `Surface`, `Card`, `Panel`, `Sheet`, `Overlay`, `Text`/`Heading`/`Mono`/`SerifBlock`, `SemanticHue` tokens, `AtmosphereProvider`, `Motion`/`Spring`, `LoadingPulse`, `FreshnessBadge`, `QualityBadge`. Plus the **Provenance Underline** CSS classes (`.prov-single/.prov-corroborated/.prov-inference/.prov-simulated/.prov-degraded`) consumed by M4's `Claim`.
- **Hooks Required:** `useAtmosphere()`, `useReducedMotion()`, `useSpring()`, `useLocale()`.
- **State Management:** Atmosphere lives in `sessionSlice`; otherwise stateless (tokens + CSS).
- **APIs Consumed:** None (presentation; renders `Claim[]`/provenance shapes supplied by others).
- **Dependencies:** M0 (shell provides the `AtmosphereProvider` mount).
- **Reusable UI Components:** It *defines* the reusable primitives.
- **Design System Components Used:** Itself.
- **Complexity:** Medium (high in volume / breadth, not in risk).
- **Recommended Build Order:** **M1** (parallel with M0–M2; never blocks on backend).

---

## M2 · API & Data Layer

- **Module Name:** `data-layer`
- **Purpose:** The single client-side gateway to the BFF, implementing the three-protocol contract (F-05): a **REST client** (commands + simple reads, mandatory `Idempotency-Key` on every POST, `409 idempotency_conflict` handling, two-key versioning via `/v1` URI + `PMOS-Version` date header), a **GraphQL client** (persisted-queries-only in production, complexity-budget awareness, DataLoader-friendly query shapes), and an **SSE streaming manager** (resumable via `Last-Event-ID`, 15s heartbeats, 15-min termination + reconnect, stream-ticket auth). Implements the one async-job grammar (`202` + job resource + SSE progress + cancel). Points at the dev fixture by default; `NEXT_PUBLIC_PMOS_API_URL` swaps to a real BFF with an identical contract.
- **Screens Included:** None.
- **Components Included:** `RestClient`, `GraphQLClient`, `SSEManager`, `JobClient` (async-job grammar wrapper), `IdempotencyKeyProvider`, `ClaimCodec` (parses/validates `Claim[]` on the wire), `ErrorBoundaryAdapter` (maps the one standard error format to UI states).
- **Hooks Required:** `useQuery()`/`useLens()` (GraphQL persisted-query reads), `useCommand()` (REST mutation with idempotency), `useStream()` (SSE subscription), `useJob()` (async-job polling/streaming), `useProvenance(claimId)`.
- **State Management:** Owns the **server-state cache** (tenant-scoped keys) and the **streaming layer** (§3.1 layers 1 & 4). Injects `TenantContext` from `sessionSlice` into every request.
- **APIs Consumed:** All of them — this module *is* the consumption layer. Concretely the full `/api/v1` REST surface, `/api/graphql`, and every SSE endpoint (`/api/v1/ask/stream`, run/job progress, the Tide).
- **Dependencies:** M0; the dev fixture.
- **Reusable UI Components:** None (non-visual), but exposes the hooks every visual module uses.
- **Design System Components Used:** `FreshnessBadge`, `QualityBadge` are populated from server metadata it surfaces.
- **Complexity:** High
- **Recommended Build Order:** **M2** (parallel with M0/M1).

---

## M3 · Authentication & Session

- **Module Name:** `auth-session`
- **Purpose:** Clerk integration and the session-exchange flow (F-02), the route guards (`<RequireAuth>`, `<RequireRole>`), stream-ticket minting, and the honest re-auth/deprovision states. Establishes `sessionSlice` (identity, claims, authority, tenant context) that the data layer and guards read. Passwords never touch PMOS.
- **Screens Included:** Login (SSO entry), SSO callback / session-exchange, MFA challenge (Clerk-hosted; PMOS renders the surrounding states), "access removed" / re-auth states.
- **Components Included:** `LoginSurface`, `SSOCallbackHandler`, `RequireAuth`, `RequireRole`, `SessionProvider`, `ReAuthPrompt`, `DeprovisionedState`.
- **Hooks Required:** `useSession()`, `useAuthority()`, `useRequireRole()`, `useStreamTicket()`.
- **State Management:** Owns `sessionSlice`. Coordinates with M2 on 401 → silent refresh → preserve `draftSlice`.
- **APIs Consumed:** `GET /api/v1/session` (resolve claims + `TenantContext`); `POST /api/v1/stream-tickets`; Clerk SSO/OIDC handshake (external).
- **Dependencies:** M0, M2.
- **Reusable UI Components:** `Button`, `Surface`, `EmptyState` (for access-removed).
- **Design System Components Used:** `AtmosphereProvider`, typography wrappers.
- **Complexity:** Medium
- **Recommended Build Order:** **M3**.

---

## M4 · Shared Component Library (Claim / Provenance / Approval semantics)

- **Module Name:** `shared-components`
- **Purpose:** The tier-2 PMOS-semantic components (§2.2) that make the spec invariants structural: `Claim`/`ClaimList` (the only way AI prose is ever rendered), the Provenance Underline application, `MetricValue` (numbers-are-tools enforcement), `AutonomyBadge`, `ApprovalGate`, `FindingCard`, `EmptyState`. Every downstream module imports from here, so the two invariants are inherited, not re-implemented.
- **Screens Included:** None (component library); ships a gallery.
- **Components Included:** `Claim`, `ClaimList`, `ProvenanceLensTrigger`, `MetricValue`, `AutonomyBadge`, `ApprovalGate`, `FindingCard`, `EmptyState`, `CitationChip`, `ConfidenceMeter`, `ContrarianProbePanel` (the "evidence against" display used by PRD/Prioritization).
- **Hooks Required:** `useClaim()`, `useProvenance(claimId)` (re-exported from M2), `useApproval(runId)` (Approve/Edit/Decline + edit-distance capture), `useMetric(metricRef)`.
- **State Management:** Edit-distance baselines for `ApprovalGate` live in `draftSlice`. Otherwise presentational.
- **APIs Consumed:** `GET /api/v1/provenance/{id}` (via M2); metric-store reads (via GraphQL resolvers / `MetricValue`); `POST /api/v1/approvals/{runId}` (via `ApprovalGate`).
- **Dependencies:** M1 (primitives + Provenance Underline classes), M2 (provenance/metric/approval calls).
- **Reusable UI Components:** It *is* the reusable tier-2 set.
- **Design System Components Used:** `SerifBlock`, `Mono`, `SemanticHue`, the Provenance Underline classes, `Sheet`/`Overlay` (for the lens trigger).
- **Complexity:** Medium
- **Recommended Build Order:** **M4**.

---

## M5 · Global State Architecture

- **Module Name:** `state-core`
- **Purpose:** Stand up the store topology of §3 — the lightweight client slices (`sessionSlice`, `lineSlice`, `navigationSlice`, `overlaySlice`, `runSlice`, `askSlice`, `tideSlice`, `canvasSlice`, `draftSlice`) and the discipline that server data lives only in M2's query cache. Provides the SSE-event → slice reducers used by streaming surfaces.
- **Screens Included:** None.
- **Components Included:** `StoreProvider`, the slice definitions, `StreamReducerRegistry` (maps SSE event types to slice updates).
- **Hooks Required:** Per-slice selector hooks (`useLineState()`, `useRunState(runId)`, `useAskSession()`, `useTide()`, `useCanvasViewport()`, `useDraft()` …).
- **State Management:** It *is* the client-state module.
- **APIs Consumed:** None directly; consumes M2's stream events.
- **Dependencies:** M2 (streaming layer), M3 (session slice).
- **Reusable UI Components:** None.
- **Design System Components Used:** None.
- **Complexity:** Medium
- **Recommended Build Order:** **M5**.

---

## M6 · The Line (Go / Ask / Do Command Interface)

- **Module Name:** `the-line`
- **Purpose:** The single command interface (F-33), `⌘K` from any surface, three blended modes — **Go** (navigate/search, <50ms), **Ask** (stream claims, routes to M13), **Do** (dispatch an agent task, routes to M17/M20). The signature interaction and the entry point to every capability; 100% pointer-free.
- **Screens Included:** The Line overlay (mounts in `LineHost`); routes results to target surfaces.
- **Components Included:** `LineOverlay`, `LineInput`, `IntentRouter` (Go/Ask/Do classification display), `GoResults`, `AskLauncher`, `DoLauncher`, `DisambiguationPrompt`.
- **Hooks Required:** `useLine()` (open/close, mode), `useIntent(input)` (Go/Ask/Do routing), `useGoSearch(query)` (<50ms search), `useCommandHistory()`.
- **State Management:** `lineSlice`. Hands off to `askSlice` (Ask) and `runSlice` (Do).
- **APIs Consumed:** Search (Go) — `GET /api/v1/search` (<50ms); Ask — SSE `/api/v1/ask/stream`; Do — `POST /api/v1/runs` (`202`).
- **Dependencies:** M4, M5; for full value M12 (Search), M13 (Ask), M17 (runs) — but the shell scaffolds against the dev fixture first.
- **Reusable UI Components:** `Overlay`, `TextField`, `EmptyState` ("retrieval failed — try Ask"), `FindingCard`.
- **Design System Components Used:** `Motion` (luminous-pulse on Ask thinking), typography wrappers, `Mono` for object IDs.
- **Complexity:** Medium
- **Recommended Build Order:** **M6**.

---

## M7 · The Brief (System-Speaks-First Home)

- **Module Name:** `the-brief`
- **Purpose:** The default landing surface (F-32) — "the system speaks first." Leads with the top finding (what changed · what it means · what's recommended), every claim provenance-linked, re-rendered from the ledger (never stored stale), with recommended actions inline. Cold load to interactive Brief <1.5s. Half the provable wedge ROI.
- **Screens Included:** The Brief (home), recommended-action surfaces, the freshness/staleness state.
- **Components Included:** `BriefLens`, `FindingStack` (ranked `FindingCard`s), `RecommendedActions`, `BriefFreshness`, `DigestSettings`.
- **Hooks Required:** `useBriefLens()` (GraphQL persisted query), `useFindingActions()`.
- **State Management:** Server cache (Brief lens). No client slice beyond `overlaySlice` for drill-ins.
- **APIs Consumed:** GraphQL **Brief lens**; `GET /api/v1/provenance/{id}` on drill-in; notify/digest reads.
- **Dependencies:** M4, M5; data from M13/M23 (decisions) and later M10/Sentinel for findings. Scaffolds against fixture findings first.
- **Reusable UI Components:** `FindingCard`, `ClaimList`, `ProvenanceLensTrigger`, `FreshnessBadge`, `MetricValue`.
- **Design System Components Used:** `SerifBlock` (narrative), `SemanticHue`, `Motion`.
- **Complexity:** Medium
- **Recommended Build Order:** **M7**.

---

## M8 · The Meridian Canvas & Altitudes

- **Module Name:** `meridian-canvas`
- **Purpose:** The one spatial canvas (F-35), many lenses. One horizontal axis (left = evidence/past, right = plans/future; outcomes flow right→left to the decisions that predicted them). Three altitudes — Org (30k ft) / Stream (3k ft) / Object (ground). Pan/zoom ≥60fps. The product's structural answer to "many apps in a trenchcoat."
- **Screens Included:** The Meridian canvas at all three altitudes; the Object-altitude host that frames Decision Sheets / PRDs / Stories.
- **Components Included:** `MeridianCanvas`, `AltitudeProvider`, `MeridianAxis`, `CanvasViewport` (pan/zoom engine), `ObjectNode`, `OutcomeFlow` (right→left animation), `AltitudeTransition`.
- **Hooks Required:** `useCanvasViewport()`, `useAltitude()`, `useCanvasLens(streamId, altitude)`, `useTimeScrubber()`.
- **State Management:** `canvasSlice` (pan/zoom, viewport, selection, altitude). Lens data from server cache.
- **APIs Consumed:** GraphQL **Stream Canvas lens** (persisted, complexity-budgeted); time-scrubber re-queries against historical projections (`?t=`).
- **Dependencies:** M4, M5. Largest frontend track alongside M1; start early.
- **Reusable UI Components:** `Surface`, `Card`, `MetricValue`, `Claim`.
- **Design System Components Used:** `Motion`/`Spring` (the spatial physics), `SemanticHue` (Violet for simulated nodes), `AtmosphereProvider` (Midnight for monitoring).
- **Complexity:** High
- **Recommended Build Order:** **M8**.

---

## M9 · Streams, Lenses & Containers

- **Module Name:** `streams-lenses`
- **Purpose:** The container model (F-36): **Stream** (the only human-curated container — a durable area of responsibility), **Lens** (a saved, shareable canvas configuration), **Brief** (generated ephemeral narrative). Organizing without folders. Workspace = one company = one graph.
- **Screens Included:** Stream switcher / Stream view, "Save this view as a Lens" dialog, Lens share dialog.
- **Components Included:** `StreamSwitcher`, `StreamCurationPanel`, `SaveLensDialog`, `ShareLensDialog`, `LensList`.
- **Hooks Required:** `useStreams()`, `useStream(streamId)`, `useLenses()`, `useSaveLens()`, `useShareLens()`.
- **State Management:** `navigationSlice` (current Stream/Lens). Stream/Lens definitions in server cache.
- **APIs Consumed:** REST Stream/Lens commands (`POST/PATCH /api/v1/streams`, `/api/v1/lenses`, share endpoints — all idempotent); GraphQL for rendering.
- **Dependencies:** M8 (canvas it configures), M5.
- **Reusable UI Components:** `Panel`, `ComboBox`, `EmptyState`.
- **Design System Components Used:** typography wrappers, `Motion`.
- **Complexity:** Medium
- **Recommended Build Order:** **M9**.

---

## M10 · The Tide & Meridian Bar

- **Module Name:** `tide-meridian-bar`
- **Purpose:** The Tide (F-37) — calm, ranked notifications that interrupt only for Vermilion (contradiction/risk), delivered over SSE. The Meridian Bar — the persistent bottom strip: waypoints `⌘1–5`, time scrubber, altitude control. Together they are the event-driven navigation and the calm-authority UX promise.
- **Screens Included:** The Tide rail (persistent), the Meridian Bar (persistent), Vermilion interrupt modal.
- **Components Included:** `TideRail`, `TideItem`, `VermilionInterrupt`, `MeridianBar`, `WaypointNav`, `TimeScrubber`, `AltitudeControl`.
- **Hooks Required:** `useTide()` (SSE subscription, ranking, interruption gating), `useWaypoint()`, `useTimeScrubber()`, `useAltitudeControl()`.
- **State Management:** `tideSlice` (ranked items, Vermilion gating, unread), `navigationSlice` (waypoint, scrubber, altitude).
- **APIs Consumed:** SSE **Tide stream** (resumable, heartbeats); item lineage reads to source runs.
- **Dependencies:** M4, M5; Sentinel (V2) enriches it later but ranking ships with MVP signals.
- **Reusable UI Components:** `FindingCard`, `Card`, `SemanticHue` (Vermilion gate), `Motion`.
- **Design System Components Used:** `SemanticHue`, `Motion` (calm arrivals; Vermilion is the only interrupting motion), `Mono` (scrubber timestamps).
- **Complexity:** Medium
- **Recommended Build Order:** **M10**.

---

## M11 · Provenance Lens

- **Module Name:** `provenance-lens`
- **Purpose:** The openable evidence overlay (F-14, frontend portion) consumed by *every* surface that renders a `Claim`. Resolves in <400ms (enforced perf budget). Shows source chunks, ACL-trimmed with honest "n sources withheld by permissions," and the evidence-weight encoding behind the Provenance Underline. "Evidence is a material, not a feature" — there is no separate citations panel; this is it.
- **Screens Included:** The Provenance Lens overlay (intercepting route `@overlay/provenance/[claimId]`), the withheld-sources state.
- **Components Included:** `ProvenanceLens`, `SourceChunk`, `WithheldNotice`, `EvidenceWeightLegend`, `CitationGraph` (claim ↔ sources).
- **Hooks Required:** `useProvenance(claimId)`, `useSourceChunk(chunkId)`.
- **State Management:** `overlaySlice` (lens stack). Provenance data in server cache (aggressively prefetched on `Claim` hover to hit <400ms).
- **APIs Consumed:** `GET /api/v1/provenance/{id}`.
- **Dependencies:** M4 (the `Claim`/trigger), M2.
- **Reusable UI Components:** `Overlay`, `Sheet`, `SerifBlock`, `Mono`, `CitationChip`.
- **Design System Components Used:** Provenance Underline classes, `SemanticHue`, `Motion` (arrives from the clicked claim, returns to it).
- **Complexity:** Medium
- **Recommended Build Order:** **M11**.

---

# Group B — Knowledge Platform

*The ingestion-fed knowledge surfaces: connector admin, search, Ask-the-Brain, Feedback Intelligence (the wedge), and the free Diagnostic. Maps to F-08–F-13 (frontend portions), F-22, F-23, F-29.*

---

## M12 · Search (Line "Go")

- **Module Name:** `search`
- **Purpose:** The "Go" mode of the Line (F-13/F-33, Flow 3) — sub-50ms navigation/lookup across the graph. The IA's only browsing mechanism; there are no folders to click. Results are objects (decisions, PRDs, features, accounts), keyboard-navigable.
- **Screens Included:** Go-mode results within the Line overlay; the honest empty state.
- **Components Included:** `GoResultsList`, `GoResultRow`, `ResultTypeBadge`, `KeyboardResultNav`.
- **Hooks Required:** `useGoSearch(query)` (debounced, <50ms target), `useResultNavigation()`.
- **State Management:** `lineSlice` (query, results, selection). Results cached briefly in server cache.
- **APIs Consumed:** `GET /api/v1/search` (Go); tiered search served from indexes (no LLM in the hot path).
- **Dependencies:** M6 (the Line), M2.
- **Reusable UI Components:** `EmptyState`, `Mono` (IDs), `SemanticHue` (type badges).
- **Design System Components Used:** typography wrappers, `Motion`.
- **Complexity:** Medium
- **Recommended Build Order:** **M12**.

---

## M13 · Ask-the-Brain (Line "Ask")

- **Module Name:** `ask-the-brain`
- **Purpose:** The Org-Wide Product Brain (F-29, Flow 4) — anyone asks "why don't we support SSO on Starter?" and gets decision + evidence + owner + review date. First token <700ms; claims stream over SSE, each provenance-linked; honest abstention ("n sources withheld" / "I can't ground this"). Ubiquity is the expansion engine — free unlimited viewers using Ask is how PMOS spreads org-wide.
- **Screens Included:** The Ask streaming surface (intercepting `@overlay/ask`), the abstention state, the degraded-model quality-badge state.
- **Components Included:** `AskSurface`, `StreamingClaimList`, `AbstentionNotice`, `WithheldNotice`, `AskQualityBadge`, `AskHistory`.
- **Hooks Required:** `useAskSession()` (SSE accumulation of `Claim[]`), `useAbstention()`, `useStreamTicket()` (for `EventSource` cases).
- **State Management:** `askSlice` (in-flight sessions, accumulated claims, abstention/quality state).
- **APIs Consumed:** SSE `/api/v1/ask/stream` (stream-ticket auth, resumable); `GET /api/v1/provenance/{id}`.
- **Dependencies:** M6 (Line "Ask"), M11 (provenance), M4 (`ClaimList`), M5 (`askSlice`).
- **Reusable UI Components:** `ClaimList`, `ProvenanceLensTrigger`, `QualityBadge`, `EmptyState`.
- **Design System Components Used:** `Motion` (luminous-pulse thinking, claims arriving), `SerifBlock`, `SemanticHue` (Signal Blue for live AI).
- **Complexity:** Medium
- **Recommended Build Order:** **M13**.

---

## M14 · Connectors & Ingestion Admin

- **Module Name:** `connectors-admin`
- **Purpose:** The admin surface for the Connector SDK and ingestion pipeline (F-08, F-09, frontend portions) — Olivia connects the org's real tools (Zendesk, Jira, Notion, Confluence, Slack, Linear; Gong/Salesforce as honestly-surfaced coverage *upgrades*), sees coverage estimates ("+ Gong would raise customer-voice coverage 41% → 78%"), monitors connector health, and reviews the injection-screening quarantine.
- **Screens Included:** Connectors settings / "Add a source," OAuth consent hand-off (external), coverage/health view, quarantine review.
- **Components Included:** `ConnectorList`, `AddConnectorFlow`, `OAuthHandoff`, `CoverageEstimate`, `ConnectorHealthCard`, `QuarantineReview`, `IngestionLaneStatus`.
- **Hooks Required:** `useConnectors()`, `useConnectorHealth(id)`, `useAddConnector()`, `useQuarantine()`, `useCoverageEstimate()`.
- **State Management:** Server cache (connectors, health, quarantine). No bespoke slice.
- **APIs Consumed:** `POST /api/v1/connectors` (Idempotency-Key) + OAuth callback; `GET /api/v1/connectors/{id}/health`; `GET /api/v1/ingestion/quarantine`.
- **Dependencies:** M2, M3 (`<RequireRole editor>`), M26 (admin shell) ideally.
- **Reusable UI Components:** `Panel`, `Card`, `Toggle`, `MetricValue` (coverage %), `FreshnessBadge`.
- **Design System Components Used:** `SemanticHue` (Amber for degraded/expiring connectors, Vermilion for failed), typography wrappers.
- **Complexity:** Medium
- **Recommended Build Order:** **M14**.

---

## M15 · Feedback Intelligence (the wedge)

- **Module Name:** `feedback-intelligence`
- **Purpose:** The wedge (F-22, Flow 5) — every signal clustered, quantified, tied to accounts/revenue, threaded to the decisions it should inform. Sam opens the Brief, sees "Billing friction up 3× on enterprise accounts ($1.2M ARR)," drills into the cluster, into atoms (each cited), and threads the cluster to a decision or PRD in one move. Must prove ≥5–6 hrs/PM/week recovered on tickets+docs alone.
- **Screens Included:** Feedback cluster lens (`/feedback`), cluster detail, atom drill-in, the thread-to-decision/PRD action.
- **Components Included:** `FeedbackClusterLens`, `ClusterCard`, `AtomList`, `AtomDetail`, `RevenueQuantification`, `ThreadToDecision`, `ClusterConfidence`.
- **Hooks Required:** `useFeedbackLens()` (GraphQL), `useCluster(id)`, `useThreadToDecision()`, `useClusterMetrics()` (account/revenue join via metric store).
- **State Management:** Server cache (feedback lens, clusters). Threading action via `useCommand()`.
- **APIs Consumed:** GraphQL **feedback lens**; `GET /api/v1/provenance/{id}`; metric-store tool-call results surfaced through `MetricValue`.
- **Dependencies:** M11 (provenance), M4, M5; backed by F-10/F-11/F-13/F-15.
- **Reusable UI Components:** `ClaimList`, `MetricValue` (revenue/ARR — never free-text numbers), `ProvenanceLensTrigger`, `FindingCard`, `ConfidenceMeter`.
- **Design System Components Used:** `SemanticHue` (Amber/Vermilion for risk thresholds), `Motion`, `SerifBlock`.
- **Complexity:** High
- **Recommended Build Order:** **M15**.

---

## M16 · The Free Diagnostic (GTM)

- **Module Name:** `diagnostic`
- **Purpose:** The go-to-market wedge (F-23) — a free, self-serve diagnostic that ingests a prospect's accessible sources (read-only) and surfaces a cited findings report ("here's what you're not seeing," with coverage estimate and honest gaps) before purchase. Runs on the standard async-job grammar; reachable pre-auth from the marketing site.
- **Screens Included:** "Run the Diagnostic" entry, connector consent (read-only), diagnostic progress (async job + SSE), findings report.
- **Components Included:** `DiagnosticLauncher`, `ReadOnlyConnectorConsent`, `DiagnosticProgress`, `FindingsReport`, `CoverageGapPanel`.
- **Hooks Required:** `useDiagnostic()` (`useJob()` wrapper — `202` + SSE progress + cancel), `useDiagnosticReport()`.
- **State Management:** Job state via `useJob()`; report in server cache. Trial-scoped, isolated.
- **APIs Consumed:** `POST /api/v1/diagnostic` (`202` + job + SSE + cancel); report via GraphQL.
- **Dependencies:** M14 (connector consent), M15 (synthesis surfaces), M2 (job grammar).
- **Reusable UI Components:** `FindingCard`, `ClaimList`, `MetricValue` (coverage), `ProvenanceLensTrigger`.
- **Design System Components Used:** `Motion` (progress as luminous pulse), `SerifBlock`, `FreshnessBadge`.
- **Complexity:** Medium
- **Recommended Build Order:** **M16**.

---

# Group C — AI Workspace

*The agent-facing surfaces: the run console (lifecycle, SSE progress, L2 approvals), the PRD and Story studios, the Conductor/Research surfaces, and one-way Living Sync. Maps to F-17–F-20 (frontend portions), F-24, F-25, F-26, F-27, F-31.*

---

## M17 · Agent Run Console

- **Module Name:** `run-console`
- **Purpose:** The frontend of the agent runtime (F-17/F-06/F-18/F-19, frontend portions) — the run lifecycle surface every agent shares: dispatch, live SSE step-trace progress, the L2 **approval gate**, autonomy/level display, parent→child run trees (Conductor), and honest partial-result/failure states. "Autonomy is visible, graduated, and reversible" — the user can always answer *what is the AI doing, under what authority, how do I take the wheel.*
- **Screens Included:** Run progress (overlay `@overlay` or `/runs/[runId]`), step trace, approval prompt, assembled review package (Conductor), run-failure/partial state.
- **Components Included:** `RunConsole`, `RunProgress`, `StepTrace`, `RunTree` (parent/child), `ApprovalPrompt` (wraps `ApprovalGate`), `AutonomyDisplay`, `RunFailureState`, `TakeTheWheel` (halt/redirect control).
- **Hooks Required:** `useRun(runId)` (SSE progress accumulation), `useRunTree(parentId)`, `useApproval(runId)`, `useDispatchRun()`.
- **State Management:** `runSlice` (active runs, step traces, parent/child). Approvals capture edit-distance into `draftSlice`.
- **APIs Consumed:** `POST /api/v1/runs` (`202`); SSE run progress (`GET /api/v1/runs/{id}/stream`); `GET /api/v1/runs/{id}`; `POST /api/v1/approvals/{runId}`.
- **Dependencies:** M2 (run/job client), M5 (`runSlice`), M4 (`AutonomyBadge`, `ApprovalGate`), M11.
- **Reusable UI Components:** `AutonomyBadge`, `ApprovalGate`, `ClaimList`, `Mono` (run IDs, step IDs), `FindingCard` (assembled package).
- **Design System Components Used:** `Motion` (luminous-pulse thinking per step), `SemanticHue` (Verdant approved / Amber pending / Vermilion failed), `AtmosphereProvider` (Midnight for monitoring runs).
- **Complexity:** High
- **Recommended Build Order:** **M17**.

---

## M18 · PRD Studio (PRD Agent, L1/L2)

- **Module Name:** `prd-studio`
- **Purpose:** The Evidence-Native PRD surface (F-24, Flow 6) — a committed decision becomes a build-ready PRD where every sentence traces to evidence (`Claim[]`), with a mandatory **contrarian probe** ("evidence against") displayed alongside. L1 draft / L2 approve; the PM edits non-goals (edit-distance logged), then approves; the PRD is versioned and flagged `ai_generated` + `source_run_id`. Serif artifact typography — documents read like the record.
- **Screens Included:** PRD draft/review surface (Object altitude), section editor, contrarian-probe panel, approval prompt, version history.
- **Components Included:** `PRDStudio`, `PRDSectionEditor`, `ContrarianProbePanel`, `NonGoalsEditor`, `PRDApproval`, `PRDVersionHistory`, `EditDistanceIndicator`.
- **Hooks Required:** `usePRDRun(decisionId)`, `usePRDDraft(runId)`, `useApproval(runId)`, `useEditDistance()`, `usePRDVersions(prdId)`.
- **State Management:** `draftSlice` (un-approved edits + baseline for edit-distance), `runSlice` (the PRD run). Versioned PRD in server cache.
- **APIs Consumed:** `POST /api/v1/runs` (PRD task, `202`); SSE progress; `POST /api/v1/approvals/{runId}`; `POST /api/v1/prds` (on approval/commit); GraphQL for the versioned read.
- **Dependencies:** M17 (run lifecycle + approval), M11 (provenance), M4 (`ContrarianProbePanel`, `ClaimList`), M22 (entry from Decision Sheet).
- **Reusable UI Components:** `ClaimList`, `ContrarianProbePanel`, `ApprovalGate`, `AutonomyBadge`, `ProvenanceLensTrigger`, `MetricValue`.
- **Design System Components Used:** `SerifBlock` (the artifact body), Provenance Underline classes, `SemanticHue` (inference vs verified), `Motion`.
- **Complexity:** High
- **Recommended Build Order:** **M18**.

---

## M19 · Story Studio (Story Agent, L1/L2)

- **Module Name:** `story-studio`
- **Purpose:** The Story Writing surface (F-25, Flow 7) — an approved PRD becomes engineer-grade epics/stories/ACs, generated as evidence-native `Claim[]`. L1/L2 in Year-1 (the L3 push to Jira is V2). This is where the **edit-distance / approval-latency band** (the P4 advocacy threshold, kill/pivot at >30%) is felt; the UI must make engineer review fast and the edit-distance signal honest.
- **Screens Included:** Story review surface, epic tree, story detail, AC detail, approval prompt (Priya's quality bar), per-(team, task-type) edit-distance readout.
- **Components Included:** `StoryStudio`, `EpicTree`, `StoryCard`, `ACDetail`, `StoryApproval`, `EditDistanceReadout`, `ApprovalLatencyHint`.
- **Hooks Required:** `useStoryRun(prdId)`, `useEpicTree(runId)`, `useApproval(runId)`, `useEditDistance()`.
- **State Management:** `draftSlice`, `runSlice`. Epics/stories/requirements in server cache.
- **APIs Consumed:** `POST /api/v1/runs` (story task); SSE; `POST /api/v1/approvals/{runId}`; GraphQL for the hierarchy read.
- **Dependencies:** M18 (upstream PRD), M17, M11, M4.
- **Reusable UI Components:** `ClaimList`, `ApprovalGate`, `AutonomyBadge`, `ProvenanceLensTrigger`, `Mono` (story/AC IDs).
- **Design System Components Used:** `SerifBlock`/`Mono` (stories read like the record; ACs are mono-precise), Provenance Underline classes, `SemanticHue`.
- **Complexity:** High
- **Recommended Build Order:** **M19**.

---

## M20 · Conductor & Research Surfaces

- **Module Name:** `conductor-research`
- **Purpose:** The Conductor (F-26) and Research (F-27) agent surfaces. Conductor is the "Do"-mode brain: a user hands off a goal ("prep the billing decision"), the Conductor plans, delegates to specialists, assembles results, and submits a review package — surfaced as a parent run with child runs in M17. Research is the agent face of Feedback Intelligence: cited synthesis with a contrarian view, openable from the Line, the Brief, or Feedback Intelligence.
- **Screens Included:** The Conductor review package (assembled from child runs), the Research output surface, Provenance Lens drill-ins. (Both largely compose M17's run console.)
- **Components Included:** `ConductorPackage`, `DelegationTree` (specialization of `RunTree`), `ResearchOutput`, `ContrarianView`, `SynthesisCard`.
- **Hooks Required:** `useConductorRun(goal)`, `useResearchRun(query)`, `useRunTree(parentId)`.
- **State Management:** `runSlice` (parent/child). Research output in server cache.
- **APIs Consumed:** `POST /api/v1/runs` (orchestration + research tasks); SSE; sub-run dispatch is internal (surfaced via `RunTree`); provenance reads.
- **Dependencies:** M17 (run console), M15 (Research ↔ Feedback Intelligence), M6 (Do mode entry), M11.
- **Reusable UI Components:** `FindingCard`, `ClaimList`, `ContrarianProbePanel`, `AutonomyBadge`, `ProvenanceLensTrigger`.
- **Design System Components Used:** `Motion`, `SerifBlock`, `SemanticHue`.
- **Complexity:** Medium
- **Recommended Build Order:** **M20**.

---

## M21 · Living Sync (One-Way, Diff + Rationale, L2)

- **Module Name:** `living-sync`
- **Purpose:** The one-way Living Sync surface (F-31, Flow 10) — push the approved spec (epic tree + stories) to the execution tool (Jira/Linear/ADO) with **diffs + rationale** (`Claim[]`), behind an L2 approval before any external write. Tracks sync status. PMOS syncs, never replaces. Year-1 is one-way; the bidirectional/revert-handle version (V2) is the seam to leave open.
- **Screens Included:** Sync preview (diffs + rationale), approval prompt, sync status, partial-push / re-auth states.
- **Components Included:** `SyncPreview`, `DiffView`, `SyncRationale`, `SyncApproval`, `SyncStatus`, `ExternalRefBadge`, `PartialPushState`.
- **Hooks Required:** `useSyncJob(targetTool)` (`useJob()` wrapper, `202`), `useSyncDiff()`, `useApproval(syncRunId)`, `useSyncStatus()`.
- **State Management:** Job/sync state via `useJob()` + `runSlice`. External refs in server cache.
- **APIs Consumed:** `POST /api/v1/sync` (`202`); approval endpoint; external write executes server-side via the governed tool service (capability token) — never client-side.
- **Dependencies:** M19 (approved stories), M17 (approval), M14 (the connected execution tool).
- **Reusable UI Components:** `ClaimList` (rationale), `ApprovalGate`, `Mono` (external IDs), `SemanticHue` (Amber drift / Vermilion conflict).
- **Design System Components Used:** Diff uses `Mono` + `SemanticHue`; `Motion`; `FreshnessBadge` (sync coherence).
- **Complexity:** Medium
- **Recommended Build Order:** **M21**.

---

# Group D — Product Intelligence

*The decision-memory surfaces: the Commit Ceremony + Decision Sheet and the Decision Ledger read surfaces. Maps to F-21 (frontend portion), F-28, F-30. (Feedback Intelligence, the other "product intelligence" pillar, lives in Group B as the wedge; cross-referenced here.)*

---

## M22 · Decision Sheet & Commit Ceremony

- **Module Name:** `decision-sheet`
- **Purpose:** The ceremonial write surface (F-30, F-28) — a human commits a decision (typed initial = the ceremony; the commit writes the hash-chained ledger entry). The Decision Sheet presents **The Question** and **The Call**, supports running a **Pre-Mortem** (synthetic stakeholders — V2/F-51, scaffold the panel now), and adding guards ("gate at 5% until assumption A3 verifies"). "Ceremony only where it matters." The action that fills the ledger organically — *zero standalone "log a decision" forms.*
- **Screens Included:** Decision Sheet (intercepting `@overlay/decision/[id]` over the canvas), The Question / The Call panels, Pre-Mortem panel, guard editor, commit confirmation, signature/verification states.
- **Components Included:** `DecisionSheet`, `TheQuestion`, `TheCall`, `PreMortemPanel`, `GuardEditor`, `CommitCeremony` (typed-initial control), `CommitConfirmation`, `DissentPanel`.
- **Hooks Required:** `useDecisionSheet(id)`, `useCommit()` (ceremonial REST command, Idempotency-Key), `usePreMortem()` (V2-backed; renders Violet hypotheticals), `useGuards()`.
- **State Management:** `overlaySlice` (the sheet), `draftSlice` (un-committed guards/edits). Decision read via GraphQL **Decision Sheet lens**.
- **APIs Consumed:** GraphQL **Decision Sheet lens**; `POST /api/v1/decisions/commit` (ceremonial, Idempotency-Key, signature-verified server-side).
- **Dependencies:** M11 (provenance), M17 (dispatches follow-on PRD run), M8 (opens over canvas), backed by F-21 (audit fabric).
- **Reusable UI Components:** `ClaimList`, `MetricValue` (predicted impact), `ApprovalGate`-adjacent commit control, `ProvenanceLensTrigger`, `ContrarianProbePanel`.
- **Design System Components Used:** `SerifBlock` (the record), `SemanticHue` (Violet for Pre-Mortem hypotheticals, Verdant on commit), `Motion`, `Mono` (the ledger hash on confirmation).
- **Complexity:** Medium
- **Recommended Build Order:** **M22**.

---

## M23 · Decision Ledger (Read Surfaces)

- **Module Name:** `decision-ledger`
- **Purpose:** The read side of the Decision Ledger v1 (F-28) — every decision a first-class, versioned object (options, evidence, assumptions, predicted impact, owner, dissent, review date; "git for product decisions"). There is no authoring form; entries are a byproduct of the commit ceremony (M22). This module renders ledger entries wherever they surface — the Decision Sheet (read mode), Ask answers, and the Brief — plus review-date reminders.
- **Screens Included:** Decision read view (Object altitude), decision version/history, review-date reminders, the "why" answer shape (consumed by Ask).
- **Components Included:** `DecisionReadView`, `DecisionVersionHistory`, `AssumptionList`, `DissentView`, `ReviewDateReminder`, `LedgerEntryHash` (mono, append-only).
- **Hooks Required:** `useDecision(id)`, `useDecisionHistory(id)`, `useReviewReminders()`.
- **State Management:** Server cache (append-only ledger reads). No client slice.
- **APIs Consumed:** GraphQL decision reads; read via Ask (M13) and the Brief (M7); review-reminder notifications via the Tide.
- **Dependencies:** M22 (entries originate there), M11, M13, M7.
- **Reusable UI Components:** `ClaimList`, `MetricValue`, `Mono` (hashes), `ProvenanceLensTrigger`, `FindingCard`.
- **Design System Components Used:** `SerifBlock`, `Mono`, `SemanticHue`, Provenance Underline classes.
- **Complexity:** Medium
- **Recommended Build Order:** **M23**.

---

# Group E — Roadmap & Planning

*The planning surfaces: Prioritization (The Arena) and Roadmap (Horizon). Maps to F-39, F-40, and the prioritization/roadmap commit paths.*

---

## M24 · Prioritization — The Arena

- **Module Name:** `the-arena`
- **Purpose:** The Prioritization surface (F-40, Flow 8) — defensible rankings with explicit trade-offs and counterfactuals, every input cited from the **governed metric store** (no invented numbers), with a mandatory contrarian probe. Sam adjusts weights, then **commits an Arena ranking**, which fires a commit ceremony (M22) → ledger. Ends "prioritization theater."
- **Screens Included:** The Arena (prioritization lens, `/arena`, `⌘3`), candidate ranking, trade-off detail, weight-adjustment, commit.
- **Components Included:** `ArenaLens`, `RankedCandidateList`, `CandidateCard`, `TradeOffDetail`, `WeightAdjuster`, `CounterfactualPanel`, `ArenaCommit` (→ M22 ceremony).
- **Hooks Required:** `useArenaLens()` (GraphQL), `usePrioritizationRun()`, `useWeights()`, `useArenaCommit()`.
- **State Management:** Server cache (Arena lens); `draftSlice` (weight adjustments before commit); `runSlice` (the prioritization run).
- **APIs Consumed:** GraphQL **Arena lens**; `POST /api/v1/runs` (prioritization task); metric-store calls (surfaced via `MetricValue`); `POST /api/v1/decisions/commit` on commit.
- **Dependencies:** M11, M15 (candidate evidence), M22 (commit ceremony), M17.
- **Reusable UI Components:** `MetricValue` (every input — never free text), `ClaimList`, `ContrarianProbePanel`, `ProvenanceLensTrigger`, `ConfidenceMeter`.
- **Design System Components Used:** `SemanticHue` (Violet for counterfactuals/simulated, Amber for "unmeasurable"), `Motion`, `Mono` (scores/deltas).
- **Complexity:** High
- **Recommended Build Order:** **M24**.

---

## M25 · Roadmap — Horizon

- **Module Name:** `horizon`
- **Purpose:** The Roadmap surface (F-39, Flow 9) — the living plan: sequencing, capacity, dependencies, scenarios, traceable to evidence. Sam asks Horizon to re-sequence given a new decision; the Roadmap agent proposes sequencing with dependencies + scenarios (cited; *simulated scenarios render Violet*); Sam reviews/adjusts and commits (ledgered). Dependency-aware, with circular-dependency surfacing and honest "capacity data missing" flags.
- **Screens Included:** Horizon (roadmap lens, `/horizon`, `⌘4`), scenario view, dependency graph, capacity view, Decision Sheet on commit.
- **Components Included:** `HorizonLens`, `RoadmapTimeline`, `SequenceEditor`, `DependencyGraph`, `ScenarioView` (Violet-gated), `CapacityPanel`, `HorizonCommit`.
- **Hooks Required:** `useHorizonLens()` (GraphQL), `useRoadmapRun()`, `useScenarios()`, `useDependencies()`, `useHorizonCommit()`.
- **State Management:** Server cache (Horizon lens); `canvasSlice`-adjacent viewport for the timeline; `draftSlice` for un-committed re-sequencing; `runSlice` (roadmap run).
- **APIs Consumed:** GraphQL **Horizon lens**; `POST /api/v1/runs` (roadmap task); metric-store calls; `POST /api/v1/decisions/commit` on committed re-sequence.
- **Dependencies:** M8 (spatial/timeline rendering), M17, M24 (Prioritization feeds ranking), M22 (commit), M11.
- **Reusable UI Components:** `MetricValue` (capacity), `ClaimList`, `ProvenanceLensTrigger`, `SemanticHue` (Violet scenarios, Vermilion circular-dependency).
- **Design System Components Used:** `Motion`/`Spring` (timeline + dependency physics), `SemanticHue`, `Mono` (dates), `FreshnessBadge`.
- **Complexity:** High
- **Recommended Build Order:** **M25**.

---

# Group F — Administration

*The operator surfaces: org/workspace/team admin, billing & consumption metering, compliance & DSAR, and operations & kill switches. Maps to F-01/F-02 (admin views), F-34, F-41, F-42. These are cross-cutting and can be built in parallel from Wave 0 once auth exists — they gate deals, so they should not be left to the end.*

---

## M26 · Org / Workspace / Team Admin

- **Module Name:** `org-admin`
- **Purpose:** The administrative shell and the org/workspace/team/membership surfaces (F-01, F-02, F-36 membership) — workspace provisioning, the org-boundary view (the visible outcome of RLS), team/Stream membership, SCIM-synced member management, and role assignment. Olivia "encodes standards once, enforced everywhere."
- **Screens Included:** Admin home, workspace settings / org-boundary view, members list (SCIM-synced), team & Stream membership, role assignment.
- **Components Included:** `AdminShell` (the `/admin` layout + nav), `WorkspaceSettings`, `OrgBoundaryView`, `MemberList`, `RoleAssignment`, `TeamMembership`, `SCIMStatus`.
- **Hooks Required:** `useWorkspaces()`, `useMembers()`, `useRoles()`, `useTeams()`, `useSCIMStatus()`.
- **State Management:** Server cache (org/workspace/members). Reads `sessionSlice` authority for gating.
- **APIs Consumed:** REST org/workspace/team/member commands (`/api/v1/organizations`, `/api/v1/workspaces`, `/api/v1/teams`, `/api/v1/users`); GraphQL for membership reads.
- **Dependencies:** M3 (`<RequireRole>`), M2. Provides the `AdminShell` that M14/M27/M28/M29 nest under.
- **Reusable UI Components:** `Panel`, `Card`, `Toggle`, `Mono` (IDs), `EmptyState`.
- **Design System Components Used:** typography wrappers, `SemanticHue`, `Motion`.
- **Complexity:** Medium
- **Recommended Build Order:** **M26**.

---

## M27 · Billing & Consumption Metering

- **Module Name:** `billing-metering`
- **Purpose:** The Consumption Metering surface (F-34) — Alex sees autonomy units consumed per agent/task-type mapped to cost, with the **platform fee + metered units side by side**, and the inspectable per-run trace behind any charge (dispute resolution). Built on `agent_runs` token accounting + append-only hash-chained billing meters. Consumption ≥35% of revenue by Year-2 depends on this existing from day one.
- **Screens Included:** Usage dashboard, billing settings (platform-fee vs consumption mix), per-run cost trace, usage-threshold alerts.
- **Components Included:** `UsageDashboard`, `ConsumptionByAgent`, `BillingSettings`, `PricingMixSelector`, `RunCostTrace`, `UsageThresholdAlert`.
- **Hooks Required:** `useUsageLens()` (GraphQL), `useBillingMeters()`, `useRunCost(runId)`, `useThresholdAlerts()`.
- **State Management:** Server cache (usage lens, meters). No bespoke slice.
- **APIs Consumed:** GraphQL **usage lens**; `GET /api/v1/billing/meters` (append-only); per-run cost reads from `agent_runs`.
- **Dependencies:** M26 (admin shell), M2, M5; backed by F-06 token accounting + F-21 meters.
- **Reusable UI Components:** `MetricValue` (every figure — inspectable, never free text), `Mono` (run IDs, hashes), `Card`, `Panel`, `SemanticHue` (Amber thresholds).
- **Design System Components Used:** `Mono`, `MetricValue`, `FreshnessBadge`, typography wrappers.
- **Complexity:** Medium
- **Recommended Build Order:** **M27**.

---

## M28 · Compliance & Data Requests (DSAR)

- **Module Name:** `compliance-dsar`
- **Purpose:** The Compliance surface (F-41) — SOC 2 posture view, the GDPR erasure cascade workflow (DSAR → tombstone ≤24h, leaving typed "redacted" stubs so the audit chain stays verifiable), and audit-chain verification. Olivia handles inbound DSARs; security reviews inspect the same audit fabric sold as "insurance-grade audit."
- **Screens Included:** Compliance dashboard, DSAR / erasure workflow, audit-chain verification view, erasure completion/confirmation.
- **Components Included:** `ComplianceDashboard`, `DSARWorkflow`, `ErasureCascadeStatus`, `AuditChainVerify`, `RedactedStubView`.
- **Hooks Required:** `useDSAR()` (`useJob()` wrapper, `202`), `useAuditVerify()`, `useCompliancePosture()`.
- **State Management:** Job state via `useJob()`; posture/verification in server cache.
- **APIs Consumed:** `POST /api/v1/dsar` (`202` + job); `GET /api/v1/audit/verify`; compliance-posture reads.
- **Dependencies:** M26 (admin shell), M3, M2; backed by F-21 + F-03 purge path.
- **Reusable UI Components:** `Panel`, `Card`, `Mono` (hashes, tombstone IDs), `SemanticHue` (Verdant verified chain, Vermilion break risk), `FreshnessBadge`.
- **Design System Components Used:** `Mono`, `SemanticHue`, typography wrappers.
- **Complexity:** Medium
- **Recommended Build Order:** **M28**.

---

## M29 · Operations & Kill Switches

- **Module Name:** `operations-killswitch`
- **Purpose:** The Resilience surface (F-42) — the operations console with **per-tenant/agent/tool/level kill switches** (halt a misbehaving agent instantly), status/quality badges, and the honest-degradation display (model-provider failover → lower tier + visible quality badge, never silent). "Honest degradation, never silent staleness." The containment control for a compromised/misbehaving agent.
- **Screens Included:** Operations console, kill-switch controls, status / quality-badge view, failover-state display.
- **Components Included:** `OperationsConsole`, `KillSwitchControls` (tenant/agent/tool/level), `SystemStatus`, `QualityBadgeBoard`, `FailoverState`, `KillSwitchConfirm`.
- **Hooks Required:** `useKillSwitch()` (REST command), `useSystemStatus()` (SSE/poll), `useFailoverState()`.
- **State Management:** Server cache (status); kill-switch actions via `useCommand()`. Status may stream via SSE into a small ops slice.
- **APIs Consumed:** `POST /api/v1/ops/killswitch`; health/status reads; failover state.
- **Dependencies:** M26 (admin shell), M3 (`<RequireRole>` — on-call/operator), M2.
- **Reusable UI Components:** `Toggle` (the switches — destructive-confirm wrapped), `QualityBadge`, `SemanticHue` (Vermilion active kill / Amber degraded), `Mono`.
- **Design System Components Used:** `QualityBadge`, `SemanticHue`, `Motion` (no spinner — pulse/badge for degraded state), `AtmosphereProvider` (Midnight monitoring).
- **Complexity:** Medium
- **Recommended Build Order:** **M29**.

---

# Part 3 — Recommended Frontend Build Sequence

The sequence below orders all 30 modules from module 1 to the final module. It respects the dependency graph (Part 1 §1) while front-loading the two long poles (the design system and the canvas) and the deal-gating administration surfaces. Where modules are independent, they are grouped into a wave that can be staffed in parallel; the linear M-number is the recommended *start* order within the overall plan.

**Guiding rule (from the spec's own §20 build order):** stand up the substrate and the contract first; build the read surfaces against the dev fixture before the backend pipeline is live; bring up the AI Workspace only once the run lifecycle and `Claim[]` rendering are solid; and keep the eval-gated quality surfaces (PRD/Story studios) on the critical path because the engineer-quality bar is the central adoption risk.

### Wave 0 — Substrate (build first, mostly in parallel; nothing ships without these)

1. **M0 · App Shell & Routing Foundation** — the frame; everything mounts here.
2. **M1 · Meridian Design System** — start in parallel with M0; longest pure-frontend track.
3. **M2 · API & Data Layer** — the three-protocol client + dev fixture; unblocks every read surface.
4. **M3 · Authentication & Session** — guards + session exchange; gates all `(app)` routes.
5. **M4 · Shared Component Library** — `Claim`/Provenance/Approval semantics; the invariants made structural.
6. **M5 · Global State Architecture** — the slice topology + SSE reducers.

### Wave 1 — Core navigation & read surfaces (Core Platform)

7. **M6 · The Line** — the command interface; scaffold against the fixture, wire to Search/Ask/runs later.
8. **M7 · The Brief** — the system-speaks-first home; the first surface a user lands on.
9. **M8 · The Meridian Canvas & Altitudes** — start early (parallel with M6/M7); second long pole.
10. **M11 · Provenance Lens** — consumed by every claim-bearing surface; bring up alongside M7.
11. **M10 · The Tide & Meridian Bar** — the persistent navigation chrome + ranked notifications.
12. **M9 · Streams, Lenses & Containers** — depends on the canvas (M8).

### Wave 2 — Knowledge Platform

13. **M12 · Search (Line "Go")** — wires the Line's Go mode to indexes.
14. **M13 · Ask-the-Brain (Line "Ask")** — the org-wide brain; the ubiquity/expansion engine.
15. **M14 · Connectors & Ingestion Admin** — Olivia connects the org's real sources; can start in parallel from Wave 0 once M3 exists.
16. **M15 · Feedback Intelligence** — the wedge; the primary value-proof surface.
17. **M16 · The Free Diagnostic** — the GTM funnel; depends on connectors (M14) + synthesis (M15).

### Wave 3 — AI Workspace

18. **M17 · Agent Run Console** — the shared run lifecycle + L2 approvals; the spine of every agent surface.
19. **M18 · PRD Studio** — evidence-native PRD; on the critical path (engineer-quality bar).
20. **M19 · Story Studio** — engineer-grade stories; where the edit-distance kill/pivot signal lives.
21. **M20 · Conductor & Research Surfaces** — orchestration + the Research agent face (composes M17).
22. **M21 · Living Sync** — one-way spec → Jira/Linear/ADO with diff + rationale + L2 approval.

### Wave 4 — Product Intelligence + Roadmap & Planning

23. **M22 · Decision Sheet & Commit Ceremony** — the ceremonial write that fills the ledger organically.
24. **M23 · Decision Ledger (read surfaces)** — the "git for product decisions" read experience.
25. **M24 · Prioritization — The Arena** — defensible, cited rankings; commits via the ceremony.
26. **M25 · Roadmap — Horizon** — the living plan; depends on the canvas, prioritization, and the ceremony.

### Wave 5 — Administration (cross-cutting; M26 can start as early as Wave 1, the rest nest under it)

27. **M26 · Org / Workspace / Team Admin** — the admin shell + membership; start early (deal-gating).
28. **M27 · Billing & Consumption Metering** — usage + the pricing-mix choice; build alongside the agents.
29. **M28 · Compliance & Data Requests (DSAR)** — SOC 2 / GDPR erasure; gates enterprise deals.
30. **M29 · Operations & Kill Switches** — honest degradation + containment; the final operator surface.

---

## Parallelization notes (how to staff this)

- **Two tracks can run from day one:** a *platform/design track* (M0 → M1 → M4 → M8) and a *contract/data track* (M0 → M2 → M3 → M5). They converge at Wave 1.
- **M1 (Design System) and M8 (Canvas) are the long poles** — assign dedicated owners and start both in Wave 0; they never block on the backend pipeline (they need only the API contract and the `Claim[]` shape).
- **M14 (Connectors) and M26 (Org Admin)** can be built in parallel with the read surfaces as soon as M3 exists — they gate deals and should not be deferred to the end.
- **The AI Workspace chain (M17 → M18 → M19 → M21) is sequential** and is the second-longest pole; M17 must be solid before the studios, and the PRD/Story studios carry the eval-gated quality risk, so give them runway.
- **The Decision Sheet (M22) is a hub** — it is the commit target for Prioritization (M24) and Roadmap (M25) and the dispatch point for the PRD studio (M18); build it before those depend on a real commit path (they can scaffold against the fixture earlier).

---

## Coverage check (every Foundation/MVP/V1 feature is represented)

| Feature | Module(s) |
|---|---|
| F-01 RLS/Tenancy | M0 (tenant boundary), M26 (org-boundary view) |
| F-02 Identity/Clerk | M3, M26 |
| F-03 Core Schema | (backend; surfaced via every read surface) |
| F-04 Outbox/Events | (backend; surfaced as freshness in M2/M7) |
| F-05 BFF/3-protocol | M2 |
| F-06 AI Schema Spine | M17, M27 |
| F-07 Model Gateway | (backend; surfaced as `QualityBadge` in M13/M29) |
| F-08 Connector SDK | M14 |
| F-09 Ingestion/Screening | M14 (quarantine review) |
| F-10 Signal Extraction | (backend; surfaced via M15) |
| F-11 Entity Resolution | (backend; surfaced via M15) |
| F-12 Index Fan-Out | (backend; surfaced via M12/M13) |
| F-13 Hybrid GraphRAG | M12, M13, M15 |
| F-14 Claim[]/Provenance | M4, M11 |
| F-15 Metric Store | M4 (`MetricValue`), M15/M24/M25 |
| F-16 Memory Plane | (backend; surfaced via M17/M20) |
| F-17 Agent Runtime | M17 |
| F-18 Policy Engine/Tokens | M17 (`AutonomyBadge`/`ApprovalGate`) |
| F-19 Tool Service | M21 (external writes), M17 |
| F-20 Eval Harness | M18/M19 (edit-distance readouts) |
| F-21 Audit Fabric | M22/M23 (hashes), M27/M28 (append-only meters/chain) |
| F-22 Feedback Intelligence | M15 |
| F-23 Free Diagnostic | M16 |
| F-24 PRD Agent | M18 |
| F-25 Story Agent | M19 |
| F-26 Conductor | M20 |
| F-27 Research Agent | M20 |
| F-28 Decision Ledger | M23 |
| F-29 Ask-the-Brain | M13 |
| F-30 Commit Ceremony | M22 |
| F-31 Living Sync v1 | M21 |
| F-32 The Brief | M7 |
| F-33 The Line/Search | M6, M12 |
| F-34 Consumption Metering | M27 |
| F-35 Meridian Canvas | M8 |
| F-36 Streams/Lenses | M9, M26 (membership) |
| F-37 The Tide/Meridian Bar | M10 |
| F-38 Meridian Design System | M1 |
| F-39 Roadmap Agent | M25 |
| F-40 Prioritization Agent | M24 |
| F-41 Compliance/DSAR | M28 |
| F-42 Resilience/Kill Switches | M29 |

Features tagged "(backend)" have no dedicated end-user UI module per the Feature Inventory's note that F-03/F-04/F-07/F-10/F-11/F-12/F-16 are platform substrate exercised transitively; their frontend footprint is the freshness badges, quality badges, and cited surfaces listed against them, all delivered through M2 and M4.

---

*This breakdown is built against Year-1 implementation as authoritative, per the spec's rule of precedence. Where a module leaves a seam for a V2/Future capability (bidirectional Living Sync, the generic graph, Temporal-backed sagas, full Trust-Ladder L3/L4, the Counterfactual Simulator behind the Pre-Mortem panel), that seam is noted on the module so the forward-compatible boundary is preserved. Engineering teams can begin implementation directly from the module headers and the build sequence above.*
