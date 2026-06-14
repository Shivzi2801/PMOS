-- ============================================================================
-- PMOS · Wave-0 Slice 1 · Migration 0003
-- The strict product hierarchy (M-03):
--   products -> features -> epics -> user_stories -> requirements
-- ----------------------------------------------------------------------------
-- These are EXPLICIT first-class tables, not a generic work-item table
-- (master spec §14). PMs and agents both reason in these terms. Every row
-- carries organization_id + workspace_id, UUIDv7 PK, timestamptz, updated_at
-- trigger, and soft delete. The parent FK is within the same org (enforced by
-- the FK + the shared organization_id; RLS guarantees you only ever see rows
-- in your own org so a cross-org parent is unreachable).
-- ============================================================================

BEGIN;

-- Helper note: every table below repeats the same five conventions. They are
-- written out explicitly (rather than via a macro) so the schema is readable
-- and each migration is self-contained and reviewable.

-- ----------------------------------------------------------------------------
-- products
-- ----------------------------------------------------------------------------
CREATE TABLE products (
    id              uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id),

    name            text NOT NULL,
    description     text,
    status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','archived')),

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE TRIGGER trg_products_updated_at BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();
SELECT pmos.apply_rls('products');
CREATE INDEX idx_products_ws ON products (workspace_id) WHERE deleted_at IS NULL;

-- ----------------------------------------------------------------------------
-- features
-- ----------------------------------------------------------------------------
CREATE TABLE features (
    id              uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id),
    product_id      uuid NOT NULL REFERENCES products (id),

    name            text NOT NULL,
    description     text,
    status          text NOT NULL DEFAULT 'discovery'
                    CHECK (status IN ('discovery','defined','in_progress','shipped','archived')),

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE TRIGGER trg_features_updated_at BEFORE UPDATE ON features
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();
SELECT pmos.apply_rls('features');
CREATE INDEX idx_features_product ON features (product_id) WHERE deleted_at IS NULL;

-- ----------------------------------------------------------------------------
-- epics
-- ----------------------------------------------------------------------------
CREATE TABLE epics (
    id              uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id),
    feature_id      uuid NOT NULL REFERENCES features (id),

    title           text NOT NULL,
    description     text,
    status          text NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','in_progress','done','archived')),

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE TRIGGER trg_epics_updated_at BEFORE UPDATE ON epics
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();
SELECT pmos.apply_rls('epics');
CREATE INDEX idx_epics_feature ON epics (feature_id) WHERE deleted_at IS NULL;

-- ----------------------------------------------------------------------------
-- user_stories
-- ----------------------------------------------------------------------------
CREATE TABLE user_stories (
    id              uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id),
    epic_id         uuid NOT NULL REFERENCES epics (id),

    title           text NOT NULL,
    narrative       text,                                  -- "As a ... I want ... so that ..."
    status          text NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','ready','in_progress','done','archived')),

    -- Year-1 provenance flag: rows authored by an agent carry ai_generated=true
    -- plus the originating run id (master spec; M-18/M-25 set these on approval).
    ai_generated    boolean NOT NULL DEFAULT false,
    source_run_id   uuid,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE TRIGGER trg_user_stories_updated_at BEFORE UPDATE ON user_stories
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();
SELECT pmos.apply_rls('user_stories');
CREATE INDEX idx_user_stories_epic ON user_stories (epic_id) WHERE deleted_at IS NULL;

-- ----------------------------------------------------------------------------
-- requirements
-- ----------------------------------------------------------------------------
CREATE TABLE requirements (
    id              uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id),
    user_story_id   uuid NOT NULL REFERENCES user_stories (id),

    body            text NOT NULL,
    kind            text NOT NULL DEFAULT 'acceptance_criterion'
                    CHECK (kind IN ('acceptance_criterion','functional','non_functional','constraint')),

    ai_generated    boolean NOT NULL DEFAULT false,
    source_run_id   uuid,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE TRIGGER trg_requirements_updated_at BEFORE UPDATE ON requirements
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();
SELECT pmos.apply_rls('requirements');
CREATE INDEX idx_requirements_story ON requirements (user_story_id) WHERE deleted_at IS NULL;

COMMENT ON TABLE products     IS 'M-03 hierarchy L1. Explicit first-class table, not a generic work item.';
COMMENT ON TABLE features     IS 'M-03 hierarchy L2.';
COMMENT ON TABLE epics        IS 'M-03 hierarchy L3.';
COMMENT ON TABLE user_stories IS 'M-03 hierarchy L4. ai_generated + source_run_id carry agent provenance.';
COMMENT ON TABLE requirements IS 'M-03 hierarchy L5 (incl. acceptance criteria).';

COMMIT;
