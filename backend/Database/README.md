# PMOS · Wave-0 Slice 1 · Database Foundation

The bedrock of M-01 (Tenancy & RLS) and M-03 (Core Persistence & Product
Hierarchy). This slice ships **only** the database: schema, the isolation
invariant, partitioning, migrations, seed, and tests. No APIs, no frontend, no
later modules — exactly the manageable, deeply-understood first commit.

Everything in this slice has been applied and tested against a real
PostgreSQL 16 instance; all 13 schema/RLS assertions pass.

---

## 1. Folder structure

```
pmos-db/
├── docker-compose.yml                # local Postgres 16
├── README.md                         # this file
├── migrations/                       # AUTHORITATIVE raw-SQL migrations (ordered)
│   ├── 0001_foundation_conventions.sql   # extensions, UUIDv7, triggers, RLS helpers, roles
│   ├── 0002_organizations_workspaces.sql # tenancy boundary tables
│   ├── 0003_product_hierarchy.sql        # products→features→epics→user_stories→requirements
│   ├── 0004_documents.sql                # documents + immutable document_versions
│   └── 0005_document_chunks_partitioned.sql  # hash-partitioned Postgres↔Qdrant contract
├── seed/
│   └── seed.sql                      # two orgs (Acme, Globex) with full hierarchy
├── tests/
│   └── schema_rls_test.sql           # 13 assertions, run as role pmos_app
├── prisma/
│   └── schema.prisma                 # typed client ONLY (not migrate) — see file header
└── scripts/
    ├── migrate.sh                    # ordered, idempotent migration runner
    ├── test.sh                       # build throwaway DB → migrate → seed → assert
    └── check-no-prisma-migrate.sh    # CI guard: raw SQL stays authoritative
```

---

## 2. SQL migrations (what each one establishes)

**0001 — Foundation conventions.** Installs `pgcrypto`; the `pmos` schema; the
RFC-9562 `pmos.uuid_generate_v7()` generator (drop-in replaceable by native
`uuidv7()` on PG18); the `set_updated_at()` trigger; the tenant-context
accessors `current_org_id()` / `current_workspace_id()` (which return NULL,
not an error, when unset — the basis of the refusal posture); the `pmos_app`
(RLS-applies) and `pmos_worker` (BYPASSRLS, code-review-gated) roles; and the
single canonical `pmos.apply_rls(regclass)` function that every content table
calls once. The policy is defined in exactly one place so no table can drift.

**0002 — Tenancy boundary.** `organizations` (the hard RLS boundary;
`organization_id` is a STORED generated mirror of `id` so the canonical policy
applies verbatim; `residency_region` is write-once via trigger; `cell_id`
ships day one as the Year-2 migration seam). `workspaces` (the soft app-layer
boundary, still under the hard org policy).

**0003 — Product hierarchy.** The five explicit, first-class tables
`products → features → epics → user_stories → requirements`. Each carries the
full convention set and the canonical RLS policy. `user_stories` and
`requirements` carry `ai_generated` + `source_run_id` for agent provenance.

**0004 — Documents.** `documents` (logical handle, no content column) and
`document_versions` (immutable, append-only snapshots with `content_hash` for
embedding dedupe). The `documents.current_version_id` pointer is wired after
both tables exist.

**0005 — Document chunks.** `document_chunks`, **HASH-partitioned on
`organization_id`** into 8 partitions from the first migration (never
retrofitted). `chunk.id` *is* the Qdrant point id (composite PK
`(id, organization_id)` because partitioned tables require the partition key in
the PK). Carries `read_principals` for pre-fusion ACL trim. The canonical RLS
policy is applied to the parent **and to every partition explicitly**, so even
direct-partition access is isolated (the audit's "RLS-inheritance verification
on partitions").

---

## 3. Prisma schema

`prisma/schema.prisma` mirrors the SQL exactly and is used for the **typed
client only** in the later API wave. Raw SQL migrations remain authoritative:
Prisma cannot express UUIDv7 defaults, RLS, hash partitioning, or the triggers,
and `prisma migrate` would try to drop them. Use `prisma generate` only; the
CI guard `scripts/check-no-prisma-migrate.sh` fails the build if a
`prisma/migrations/` directory ever appears. The `DocumentChunk` model uses the
composite `@@id([id, organizationId])` to match the partitioned PK.

---

## 4. Test strategy

The suite (`tests/schema_rls_test.sql`) runs **as role `pmos_app`**, which RLS
applies to — so it tests the isolation the application actually experiences,
not the owner's god view. It is plain-SQL assertions (no pgTAP dependency);
each failure raises an exception, so `ON_ERROR_STOP=1` turns the file into a
pass/fail CI gate. Two seeded orgs (Acme, Globex) are the minimum needed to
prove cross-tenant isolation.

| # | Assertion |
|----|-----------|
| T1 | Every content table carries `organization_id` + `created_at` (conventions) |
| T2 | Every content table has `FORCE ROW LEVEL SECURITY` + the canonical policy |
| T3 | A context-less query returns **zero** rows (refusal posture) |
| T4 | Org A sees only org A rows (cross-tenant read isolation) |
| T5 | `WITH CHECK` blocks an INSERT stamped with another org's id |
| T6 | A cross-org UPDATE affects zero rows |
| T7 | All 8 `document_chunks` partitions inherit FORCE RLS |
| T8 | `document_chunks` is HASH-partitioned on `organization_id` |
| T9 | UUIDv7 version (`7`) + variant (`10xx`) bits over 500 samples |
| T10 | `residency_region` is write-once (immutability trigger fires) |
| T11 | `updated_at` trigger advances on UPDATE |
| T12 | Soft-delete partial indexes (`WHERE deleted_at IS NULL`) exist |
| T13 | `chunk.id` is resolvable as a Qdrant point id within its org |

Run the whole gate:

```bash
PGHOST=localhost PGPORT=5432 PGUSER=pmos ./scripts/test.sh
```

It creates a throwaway `pmos_test` database, migrates, seeds, and asserts.
Future waves should add: the nightly `cross-tenant-probe-worker` (any hit =
sev-0) and the `intra-tenant-probe-worker` over a user×stream×sensitivity
matrix — both need these tables to probe, which is why the substrate is first.

---

## 5. Local development instructions

**Prerequisites:** Docker (or a local PostgreSQL 16), `psql` on PATH.

```bash
# 1. Start Postgres 16
docker compose up -d
# wait for health
docker compose exec postgres pg_isready -U pmos

# 2. Point the tooling at it
export PGHOST=localhost PGPORT=5432 PGUSER=pmos PGPASSWORD=pmos PGDATABASE=pmos

# 3. Apply migrations (ordered + idempotent; safe to re-run)
./scripts/migrate.sh

# 4. Load seed data (two orgs with full hierarchy)
psql -v ON_ERROR_STOP=1 -f seed/seed.sql

# 5. Run the schema + RLS test gate (builds its own throwaway DB)
./scripts/test.sh

# 6. (optional) generate the typed Prisma client — NEVER prisma migrate
#    npx prisma generate
```

**Using the database from an app (the contract the API wave will honor):**
open a tenant-scoped transaction and set the org context before any query —

```sql
BEGIN;
SET LOCAL app.current_org_id = '<org-uuid>';
-- ... queries here see only this org's rows ...
COMMIT;
```

Connect request-path code as `pmos_app` (RLS applies). Background workers may
connect as `pmos_worker` (BYPASSRLS) but are **required by code review** to
filter `organization_id` explicitly on every query.

---

*Scope note: this is Slice 1 only. APIs, frontend, the event backbone (M-04),
audit fabric (M-21), connectors, and all later modules are deliberately out of
scope and not generated here.*
