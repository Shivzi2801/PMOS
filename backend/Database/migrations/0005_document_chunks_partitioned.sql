-- ============================================================================
-- PMOS · Wave-0 Slice 1 · Migration 0005
-- document_chunks: the Postgres<->Qdrant contract, HASH-partitioned on
-- organization_id (Readiness audit §3 / finding 2.2).
-- ----------------------------------------------------------------------------
-- This is the single most load-bearing table in the slice:
--   * chunk.id IS the Qdrant point id (master spec §14: "no mapping table").
--   * content_hash prevents redundant re-embedding.
--   * read_principals carries the source system's ACL for pre-fusion trim
--     (master spec §16; written at upsert by M-11 later — nullable now).
--   * 10^8 chunks per large tenant is the design envelope (§18), so this table
--     MUST be partitioned from the first migration, not retrofitted. The audit
--     calls retrofitting partitioning "exactly the expensive rework" to avoid.
--
-- Partitioning choice: HASH on organization_id. This spreads a single whale
-- tenant's writes across partitions while keeping every tenant's rows
-- co-located by hash bucket, which is the right shape for the org-filtered
-- retrieval reads. (Time-range partitioning is used elsewhere for append-only
-- streams like outbox/messages -- that is M-04/M-21 territory, not this slice.)
--
-- RLS on partitioned tables: the policy is declared on the PARENT and inherited
-- by every partition. We still VERIFY inheritance explicitly in the schema
-- tests (the audit's "RLS-inheritance verification on partitions").
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Parent partitioned table
-- ----------------------------------------------------------------------------
-- Note: a partitioned table's PRIMARY KEY / UNIQUE constraints must include the
-- partition key. So the PK is (id, organization_id). id alone is still globally
-- unique in practice (UUIDv7), and id remains the Qdrant point id.
CREATE TABLE document_chunks (
    id                  uuid NOT NULL DEFAULT pmos.uuid_generate_v7(),
    organization_id     uuid NOT NULL,
    workspace_id        uuid NOT NULL,

    document_id         uuid NOT NULL,
    document_version_id uuid NOT NULL,

    chunk_index         int  NOT NULL,          -- position within the version
    content             text NOT NULL,
    content_hash        text NOT NULL,          -- dedupe key for embedding

    -- Source-ACL inheritance (load-bearing security). Array of principal ids
    -- (users/groups) permitted to read this chunk at the source. Written at
    -- upsert by M-11; nullable in Slice 1 because ingestion is a later wave.
    read_principals     text[],

    -- Embedding bookkeeping (the vector itself lives in Qdrant; this row is the
    -- source of truth and is rebuildable into Qdrant at any time).
    embedded_at         timestamptz,
    embedding_model     text,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    PRIMARY KEY (id, organization_id)
) PARTITION BY HASH (organization_id);

-- updated_at trigger is declared on the parent and fires for partition writes.
CREATE TRIGGER trg_document_chunks_updated_at BEFORE UPDATE ON document_chunks
    FOR EACH ROW EXECUTE FUNCTION pmos.set_updated_at();

-- Canonical RLS on the parent -> inherited by all partitions.
SELECT pmos.apply_rls('document_chunks');

-- ----------------------------------------------------------------------------
-- Create N hash partitions. Start with 8 (a power of two eases future splits).
-- The partition-manager-worker (M-04, later wave) owns lifecycle; Slice 1
-- creates the initial set so the table is writable immediately.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    n_partitions int := 8;
    i int;
    part_name text;
BEGIN
    FOR i IN 0 .. n_partitions - 1 LOOP
        part_name := format('document_chunks_p%s', i);
        EXECUTE format($f$
            CREATE TABLE %1$s
            PARTITION OF document_chunks
            FOR VALUES WITH (MODULUS %2$s, REMAINDER %3$s);
        $f$, part_name, n_partitions, i);

        -- Enabling RLS on the partitioned PARENT enforces the policy for reads
        -- through the parent, but the child pg_class flags are not set and a
        -- role with direct access to a partition would bypass it. The audit
        -- requires RLS-inheritance VERIFIED on partitions, so we apply the same
        -- canonical policy to each partition explicitly (defense in depth).
        PERFORM pmos.apply_rls(part_name::regclass);
    END LOOP;
END;
$$;

-- Indexes are declared on the parent and propagate to every partition.
-- The Qdrant point-id contract requires fast id lookups; the retrieval reads
-- are always org-scoped, so a (organization_id, document_id) index serves the
-- common access path.
CREATE INDEX idx_document_chunks_doc
    ON document_chunks (organization_id, document_id) WHERE deleted_at IS NULL;

CREATE INDEX idx_document_chunks_version
    ON document_chunks (document_version_id);

-- Dedupe lookups by content_hash within a tenant.
CREATE INDEX idx_document_chunks_hash
    ON document_chunks (organization_id, content_hash);

COMMENT ON TABLE document_chunks IS
    'M-03 Postgres<->Qdrant contract. id = Qdrant point id. HASH-partitioned on organization_id (8 parts). read_principals drives pre-fusion ACL trim.';

COMMIT;
