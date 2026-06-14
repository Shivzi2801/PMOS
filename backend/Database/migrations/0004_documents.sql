-- ============================================================================
-- PMOS · Wave-0 Slice 1 · Migration 0004
-- Documents + immutable versions (M-03)
-- ----------------------------------------------------------------------------
-- documents is the logical handle; document_versions are IMMUTABLE snapshots
-- (master spec §14: "Documents (immutable versions + chunks)"). A document's
-- current state is its latest non-deleted version. Versions are append-only at
-- the application layer; we do not hard-enforce immutability with a trigger in
-- Slice 1 (that is M-21 audit-fabric territory), but the schema models it:
-- there is no "content" column on documents — content lives on the version.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- documents
-- ----------------------------------------------------------------------------
CREATE TABLE documents (
    id              uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id),

    title           text NOT NULL,
    doc_type        text NOT NULL DEFAULT 'note'
                    CHECK (doc_type IN ('note','prd','story_set','interview','insight','import')),

    -- Pointer to the current version (set by DocumentService on version create).
    current_version_id uuid,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
CREATE TRIGGER trg_documents_updated_at BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();
SELECT pmos.apply_rls('documents');
CREATE INDEX idx_documents_ws ON documents (workspace_id) WHERE deleted_at IS NULL;

-- ----------------------------------------------------------------------------
-- document_versions (immutable snapshots)
-- ----------------------------------------------------------------------------
CREATE TABLE document_versions (
    id              uuid PRIMARY KEY DEFAULT pmos.uuid_generate_v7(),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL REFERENCES workspaces (id),
    document_id     uuid NOT NULL REFERENCES documents (id),

    version_number  int  NOT NULL,
    content         text NOT NULL,

    -- content_hash prevents redundant re-embedding downstream (master spec §14).
    content_hash    text NOT NULL,

    -- Blob reference for user-facing assets (Supabase Storage / S3 archives);
    -- the text content above is the source of truth for chunking.
    storage_ref     text,

    created_at      timestamptz NOT NULL DEFAULT now(),
    -- No updated_at / deleted_at: versions are immutable + append-only.

    CONSTRAINT document_versions_doc_version_key UNIQUE (document_id, version_number)
);
SELECT pmos.apply_rls('document_versions');
CREATE INDEX idx_document_versions_doc ON document_versions (document_id, version_number DESC);

-- Wire the current-version pointer once both tables exist.
ALTER TABLE documents
    ADD CONSTRAINT documents_current_version_fk
    FOREIGN KEY (current_version_id) REFERENCES document_versions (id);

COMMENT ON TABLE documents         IS 'M-03 logical document handle. No content column; content lives on versions.';
COMMENT ON TABLE document_versions IS 'M-03 immutable version snapshot. content_hash dedupes embedding.';

COMMIT;
