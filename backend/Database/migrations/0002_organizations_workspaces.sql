-- ============================================================================
-- PMOS · Wave-0 Slice 1 · Migration 0002
-- Tenancy boundary tables: organizations (M-01) + workspaces (M-03)
-- ----------------------------------------------------------------------------
-- organizations is the HARD RLS boundary. It is the one content-domain table
-- whose RLS policy is self-referential: a row is visible iff its own id equals
-- the current org context. Everything else keys on organization_id.
--
-- workspaces is the first table in the M-03 hierarchy. Org -> Workspace ->
-- {Products -> Features -> Epics -> User Stories -> Requirements}.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- organizations
-- ----------------------------------------------------------------------------
CREATE TABLE organizations (
    id                  uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),

    -- The org id IS the tenant key. We carry organization_id as a generated
    -- mirror of id so the canonical RLS policy (which keys on organization_id)
    -- applies uniformly to this table too, with no special-case policy.
    organization_id     uuid GENERATED ALWAYS AS (id) STORED,

    name                text NOT NULL,
    slug                text NOT NULL,

    -- Residency region is WRITE-ONCE (master spec §17 / blueprint AC).
    -- Enforced immutable by trigger below; the public API never updates it.
    residency_region    text NOT NULL DEFAULT 'us-east-1',

    plan                text NOT NULL DEFAULT 'trial'
                        CHECK (plan IN ('trial','team','business','enterprise')),

    -- Reference handle to the Clerk org<->IdP binding (M-02 owns the binding).
    identity_binding_ref text,

    -- Per-tenant kill-switch state (M-42 operates it; the column lives here so
    -- the org row is the single place the platform reads tenant halt state).
    kill_switch_state   text NOT NULL DEFAULT 'active'
                        CHECK (kill_switch_state IN ('active','paused','halted')),

    -- Forward-compat seam (TD-1): the cell stamp ships day one (it is how
    -- staging works). Year-1 every row is 'cell-0'; Year-2 migration is an
    -- event-replay + router-flip, not a rewrite, because this column exists now.
    cell_id             text NOT NULL DEFAULT 'cell-0',

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,                       -- soft delete / GDPR

    CONSTRAINT organizations_slug_key UNIQUE (slug)
);

-- residency_region immutability guard
CREATE OR REPLACE FUNCTION pmos.guard_residency_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.residency_region IS DISTINCT FROM OLD.residency_region THEN
        RAISE EXCEPTION 'residency_region is write-once and cannot be changed (org %)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_organizations_residency_immutable
    BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION pmos.guard_residency_immutable();

CREATE TRIGGER trg_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();

-- organizations gets a self-referential RLS policy (id = current org), distinct
-- from the canonical helper which keys on a separate organization_id column.
-- Because organization_id is a STORED generated mirror of id, the canonical
-- policy works verbatim. Apply it.
SELECT pmos.apply_rls('organizations');

-- Index supporting the soft-delete-aware lookups workers do.
CREATE INDEX idx_organizations_active
    ON organizations (id) WHERE deleted_at IS NULL;

COMMENT ON TABLE organizations IS
    'M-01 hard tenant boundary. organization_id mirrors id so the canonical RLS policy applies.';

-- ----------------------------------------------------------------------------
-- workspaces
-- ----------------------------------------------------------------------------
-- One company can hold multiple workspaces; workspace is the SOFT app-layer
-- boundary (deliberate defense-in-depth split, master spec §17). RLS here is
-- still the HARD org policy; workspace scoping is enforced in the app layer.
CREATE TABLE workspaces (
    id                  uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id     uuid NOT NULL REFERENCES organizations (id),

    name                text NOT NULL,
    slug                text NOT NULL,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    -- slug unique within an org, not globally.
    CONSTRAINT workspaces_org_slug_key UNIQUE (organization_id, slug)
);

CREATE TRIGGER trg_workspaces_updated_at
    BEFORE UPDATE ON workspaces
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();

SELECT pmos.apply_rls('workspaces');

CREATE INDEX idx_workspaces_org ON workspaces (organization_id) WHERE deleted_at IS NULL;

COMMENT ON TABLE workspaces IS
    'M-03 soft (app-layer) boundary under the hard org boundary. Carries organization_id for RLS.';

COMMIT;
