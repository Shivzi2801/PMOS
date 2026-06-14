-- ============================================================================
-- PMOS · Wave-0 Slice 1 · Seed data
-- ----------------------------------------------------------------------------
-- Creates TWO organizations with a full hierarchy each. Two orgs is the
-- minimum needed to prove cross-tenant isolation: every test that matters
-- asserts org A cannot see org B.
--
-- Seeding runs as the table owner (superuser/migration role), which is subject
-- to FORCE ROW LEVEL SECURITY. So we set the org context before each org's
-- inserts, exactly as the application would. This also exercises the WITH CHECK
-- clause: an insert whose organization_id != current context is rejected.
--
-- Fixed UUIDs are used so tests can reference rows deterministically.
-- ============================================================================

BEGIN;

-- Org A: "Acme" --------------------------------------------------------------
-- Insert the org row itself. organizations.organization_id is GENERATED from
-- id, so the RLS WITH CHECK compares id against current_org_id(). We must set
-- the context to the row's own id first.
SET LOCAL app.current_org_id = '00000000-0000-7000-8000-00000000000a';

INSERT INTO organizations (id, name, slug, residency_region, plan)
VALUES ('00000000-0000-7000-8000-00000000000a', 'Acme', 'acme', 'us-east-1', 'business');

INSERT INTO workspaces (id, organization_id, name, slug)
VALUES ('00000000-0000-7000-8000-0000000000a1',
        '00000000-0000-7000-8000-00000000000a', 'Acme Core', 'core');

INSERT INTO products (id, organization_id, workspace_id, name)
VALUES ('00000000-0000-7000-8000-0000000000a2',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1', 'Checkout');

INSERT INTO features (id, organization_id, workspace_id, product_id, name)
VALUES ('00000000-0000-7000-8000-0000000000a3',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1',
        '00000000-0000-7000-8000-0000000000a2', 'One-click pay');

INSERT INTO epics (id, organization_id, workspace_id, feature_id, title)
VALUES ('00000000-0000-7000-8000-0000000000a4',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1',
        '00000000-0000-7000-8000-0000000000a3', 'Wallet integration');

INSERT INTO user_stories (id, organization_id, workspace_id, epic_id, title, narrative)
VALUES ('00000000-0000-7000-8000-0000000000a5',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1',
        '00000000-0000-7000-8000-0000000000a4',
        'Save card', 'As a shopper I want to save my card so that checkout is faster');

INSERT INTO requirements (id, organization_id, workspace_id, user_story_id, body, kind)
VALUES ('00000000-0000-7000-8000-0000000000a6',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1',
        '00000000-0000-7000-8000-0000000000a5',
        'Card data is tokenized; raw PAN never persisted.', 'non_functional');

INSERT INTO documents (id, organization_id, workspace_id, title, doc_type)
VALUES ('00000000-0000-7000-8000-0000000000a7',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1', 'Checkout PRD', 'prd');

INSERT INTO document_versions (id, organization_id, workspace_id, document_id, version_number, content, content_hash)
VALUES ('00000000-0000-7000-8000-0000000000a8',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1',
        '00000000-0000-7000-8000-0000000000a7', 1,
        'Acme checkout PRD body.', 'hash-acme-v1');

UPDATE documents SET current_version_id = '00000000-0000-7000-8000-0000000000a8'
WHERE id = '00000000-0000-7000-8000-0000000000a7';

INSERT INTO document_chunks (id, organization_id, workspace_id, document_id, document_version_id, chunk_index, content, content_hash, read_principals)
VALUES ('00000000-0000-7000-8000-0000000000a9',
        '00000000-0000-7000-8000-00000000000a',
        '00000000-0000-7000-8000-0000000000a1',
        '00000000-0000-7000-8000-0000000000a7',
        '00000000-0000-7000-8000-0000000000a8', 0,
        'Acme checkout PRD body.', 'hash-acme-v1',
        ARRAY['user:acme-pm','group:acme-product']);

-- Org B: "Globex" ------------------------------------------------------------
SET LOCAL app.current_org_id = '00000000-0000-7000-8000-00000000000b';

INSERT INTO organizations (id, name, slug, residency_region, plan)
VALUES ('00000000-0000-7000-8000-00000000000b', 'Globex', 'globex', 'eu-west-1', 'enterprise');

INSERT INTO workspaces (id, organization_id, name, slug)
VALUES ('00000000-0000-7000-8000-0000000000b1',
        '00000000-0000-7000-8000-00000000000b', 'Globex Platform', 'platform');

INSERT INTO products (id, organization_id, workspace_id, name)
VALUES ('00000000-0000-7000-8000-0000000000b2',
        '00000000-0000-7000-8000-00000000000b',
        '00000000-0000-7000-8000-0000000000b1', 'Telemetry');

INSERT INTO documents (id, organization_id, workspace_id, title, doc_type)
VALUES ('00000000-0000-7000-8000-0000000000b7',
        '00000000-0000-7000-8000-00000000000b',
        '00000000-0000-7000-8000-0000000000b1', 'Globex secret roadmap', 'note');

INSERT INTO document_chunks (id, organization_id, workspace_id, document_id, document_version_id, chunk_index, content, content_hash, read_principals)
VALUES ('00000000-0000-7000-8000-0000000000b9',
        '00000000-0000-7000-8000-00000000000b',
        '00000000-0000-7000-8000-0000000000b1',
        '00000000-0000-7000-8000-0000000000b7',
        '00000000-0000-7000-8000-0000000000b7', 0,
        'Globex confidential.', 'hash-globex-v1',
        ARRAY['user:globex-cto']);

COMMIT;
