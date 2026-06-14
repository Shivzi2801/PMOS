-- ============================================================================
-- PMOS · Wave-0 Slice 1 · Migration 0001
-- Foundation conventions: extensions, UUIDv7, audit-column trigger, RLS helpers
-- ----------------------------------------------------------------------------
-- This migration establishes the cross-cutting machinery every content table
-- inherits BEFORE any table exists, so the conventions are correct from the
-- first row. Per master spec §14 (Database Summary) and §17 (Multi-Tenancy):
--   * UUIDv7 keys everywhere (time-ordered; doubles as the Qdrant point id)
--   * timestamptz always; updated_at by trigger; soft delete via deleted_at
--   * organization_id + workspace_id on every content row
--   * canonical RLS policy + FORCE ROW LEVEL SECURITY, applied per table
--   * per-request SET LOCAL app.current_org_id inside a tenant-scoped tx
-- ============================================================================

-- Run inside a single transaction; the migration runner wraps each file.
BEGIN;

-- ----------------------------------------------------------------------------
-- Extensions
-- ----------------------------------------------------------------------------
-- pgcrypto gives us gen_random_bytes() for the UUIDv7 fallback below.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------------------------
-- Dedicated schema for tenancy machinery (keeps helpers out of public)
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS pmos;

-- ----------------------------------------------------------------------------
-- UUIDv7 generation (RFC 9562)
-- ----------------------------------------------------------------------------
-- PostgreSQL 16 does not ship a native uuidv7(). PostgreSQL 18 does. We define
-- our own deterministic, RFC-9562-conformant generator now and will DROP it in
-- favour of the native function when the platform moves to PG18 (the column
-- type and on-wire value are identical, so the swap is non-breaking).
--
-- Layout (128 bits):
--   48 bits  unix_ts_ms      big-endian millisecond timestamp
--    4 bits  version (= 7)
--   12 bits  rand_a          sub-millisecond monotonic randomness
--    2 bits  variant (= 10)
--   62 bits  rand_b          random
--
-- Time-ordering is the property M-03 relies on: PKs sort chronologically,
-- which keeps B-tree inserts append-mostly and gives the Qdrant point id a
-- natural creation order.
CREATE OR REPLACE FUNCTION pmos.uuid_generate_v7()
RETURNS uuid
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    unix_ts_ms  bigint;
    uuid_bytes  bytea;
BEGIN
    unix_ts_ms := (extract(epoch FROM clock_timestamp()) * 1000)::bigint;

    -- Start from 16 random bytes, then overwrite the timestamp + version/variant.
    uuid_bytes := gen_random_bytes(16);

    -- Bytes 0..5: 48-bit big-endian millisecond timestamp.
    -- Mask each byte in BIGINT space before casting to int, otherwise the
    -- unshifted low bytes overflow int4 (millisecond epoch > 2^31).
    uuid_bytes := set_byte(uuid_bytes, 0, ((unix_ts_ms >> 40) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 1, ((unix_ts_ms >> 32) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 2, ((unix_ts_ms >> 24) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 3, ((unix_ts_ms >> 16) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 4, ((unix_ts_ms >> 8) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 5, (unix_ts_ms & 255)::int);

    -- Byte 6: high nibble = version (0x7), low nibble = random (kept).
    uuid_bytes := set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);

    -- Byte 8: top two bits = variant (0b10), rest random (kept).
    uuid_bytes := set_byte(uuid_bytes, 8, (get_byte(uuid_bytes, 8) & 63) | 128);

    RETURN encode(uuid_bytes, 'hex')::uuid;
END;
$$;

COMMENT ON FUNCTION pmos.uuid_generate_v7() IS
    'RFC 9562 UUIDv7. Replace with native uuidv7() on PostgreSQL 18+ (non-breaking).';

-- ----------------------------------------------------------------------------
-- updated_at trigger (the convention: updated_at is maintained by trigger,
-- never trusted from the client)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION pmos.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- ----------------------------------------------------------------------------
-- Tenant-context accessors
-- ----------------------------------------------------------------------------
-- The application opens a tenant-scoped transaction and issues:
--     SET LOCAL app.current_org_id = '<uuid>';
-- Every RLS policy reads it through this helper. current_setting(..., true)
-- returns NULL (not an error) when the GUC is unset, which is what lets the
-- policy *refuse* context-less access rather than crash.
CREATE OR REPLACE FUNCTION pmos.current_org_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_org_id', true), '')::uuid;
$$;

-- Optional soft (workspace) boundary accessor. The org boundary is the HARD
-- RLS boundary; workspace is the SOFT app-layer boundary (master spec §17:
-- join-based workspace RLS degrades plans, so we keep it app-enforced and only
-- expose the GUC for the highest-sensitivity tables that opt in later).
CREATE OR REPLACE FUNCTION pmos.current_workspace_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_workspace_id', true), '')::uuid;
$$;

-- ----------------------------------------------------------------------------
-- Roles
-- ----------------------------------------------------------------------------
-- pmos_app  : the application role. RLS APPLIES to it (it is not the owner and
--             is not BYPASSRLS). All request-path queries run as this role.
-- pmos_worker: background workers. They are granted BYPASSRLS but are
--             code-review-required to filter organization_id explicitly
--             (master spec §17). We create the role so grants are explicit and
--             auditable from migration 0001.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pmos_app') THEN
        CREATE ROLE pmos_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pmos_worker') THEN
        CREATE ROLE pmos_worker NOLOGIN BYPASSRLS;
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA pmos TO pmos_app, pmos_worker;
GRANT USAGE ON SCHEMA public TO pmos_app, pmos_worker;

-- ----------------------------------------------------------------------------
-- Canonical RLS policy applicator
-- ----------------------------------------------------------------------------
-- Every content table calls this exactly once at creation time. It is the
-- SINGLE definition of the org-isolation policy so no table can drift. It:
--   1. enables RLS,
--   2. FORCES RLS (so even the table owner is subject to it),
--   3. installs the canonical USING + WITH CHECK policy keyed on organization_id.
-- The WITH CHECK clause is what stops a write from stamping a row with a
-- different tenant's organization_id.
CREATE OR REPLACE FUNCTION pmos.apply_rls(target regclass)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY;', target);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY;', target);

    -- Drop-then-create keeps the function idempotent across re-runs.
    EXECUTE format('DROP POLICY IF EXISTS rls_org_isolation ON %s;', target);
    EXECUTE format($f$
        CREATE POLICY rls_org_isolation ON %s
        USING (organization_id = pmos.current_org_id())
        WITH CHECK (organization_id = pmos.current_org_id());
    $f$, target);

    -- The application role needs row access; RLS still constrains it.
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO pmos_app;', target);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO pmos_worker;', target);
END;
$$;

COMMENT ON FUNCTION pmos.apply_rls(regclass) IS
    'Canonical org-isolation RLS. Called once per content table; the single source of the policy.';

COMMIT;
