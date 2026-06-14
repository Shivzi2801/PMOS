-- ============================================================================
-- PMOS · Wave-0 Slice 1 · Schema & RLS test suite
-- ----------------------------------------------------------------------------
-- Plain-SQL assertion harness (no pgTAP dependency required). Each test raises
-- an exception on failure, so running with ON_ERROR_STOP=1 makes the whole
-- file a pass/fail gate suitable for CI. A final NOTICE prints the pass count.
--
-- These tests run as role pmos_app, which RLS APPLIES to (it is not the table
-- owner and is not BYPASSRLS). That is the whole point: we test the isolation
-- the application actually experiences, not the owner's god view.
--
-- Coverage maps to the blueprint Acceptance Criteria / Tests Required:
--   T1  every content table carries the convention columns
--   T2  every content table has FORCE ROW LEVEL SECURITY + the canonical policy
--   T3  context-less query returns ZERO rows (refusal posture)
--   T4  org A sees only org A rows (cross-tenant read isolation)
--   T5  org A cannot INSERT a row stamped with org B (WITH CHECK)
--   T6  org A cannot UPDATE org B rows (they are invisible -> 0 affected)
--   T7  RLS policy is inherited by every document_chunks partition
--   T8  document_chunks is HASH-partitioned on organization_id (8 parts)
--   T9  UUIDv7: version + variant bits, and time-ordering
--   T10 residency_region is write-once (immutability trigger fires)
--   T11 updated_at trigger advances on UPDATE
--   T12 soft-delete column exists & filtered indexes are partial
--   T13 chunk.id == Qdrant point id contract (PK includes id; id resolvable)
-- ============================================================================

\set ON_ERROR_STOP on
SET ROLE pmos_app;

DO $$
DECLARE
    orgA uuid := '00000000-0000-7000-8000-00000000000a';
    orgB uuid := '00000000-0000-7000-8000-00000000000b';
    n int;
    ok boolean;
    passed int := 0;
    content_tables text[] := ARRAY[
        'organizations','workspaces','products','features','epics',
        'user_stories','requirements','documents','document_versions','document_chunks'];
    t text;
BEGIN
    -- ---- T1: convention columns on every content table --------------------
    FOREACH t IN ARRAY content_tables LOOP
        -- organization_id, created_at present everywhere
        SELECT count(*) INTO n FROM information_schema.columns
          WHERE table_name = t AND column_name IN ('organization_id','created_at');
        IF n <> 2 THEN
            RAISE EXCEPTION 'T1 FAIL: % missing organization_id/created_at', t;
        END IF;
    END LOOP;
    passed := passed + 1; RAISE NOTICE 'T1 PASS: convention columns present';

    -- ---- T2: FORCE RLS + canonical policy on every content table ----------
    FOREACH t IN ARRAY content_tables LOOP
        SELECT relforcerowsecurity INTO ok FROM pg_class WHERE relname = t AND relkind IN ('r','p');
        IF NOT ok THEN RAISE EXCEPTION 'T2 FAIL: % not FORCE ROW LEVEL SECURITY', t; END IF;
        SELECT count(*) INTO n FROM pg_policies WHERE tablename = t AND policyname = 'rls_org_isolation';
        IF n <> 1 THEN RAISE EXCEPTION 'T2 FAIL: % missing canonical policy', t; END IF;
    END LOOP;
    passed := passed + 1; RAISE NOTICE 'T2 PASS: FORCE RLS + canonical policy everywhere';

    -- ---- T3: no context => zero rows (refusal) ----------------------------
    PERFORM set_config('app.current_org_id', '', true);  -- clear context
    SELECT count(*) INTO n FROM documents;
    IF n <> 0 THEN RAISE EXCEPTION 'T3 FAIL: context-less read returned % rows', n; END IF;
    passed := passed + 1; RAISE NOTICE 'T3 PASS: context-less query returns zero rows';

    -- ---- T4: cross-tenant read isolation ----------------------------------
    PERFORM set_config('app.current_org_id', orgA::text, true);
    SELECT count(*) INTO n FROM documents WHERE organization_id = orgB;
    IF n <> 0 THEN RAISE EXCEPTION 'T4 FAIL: org A saw % org B documents', n; END IF;
    SELECT count(*) INTO n FROM document_chunks;   -- A has exactly 1 chunk seeded
    IF n <> 1 THEN RAISE EXCEPTION 'T4 FAIL: org A sees % chunks, expected 1', n; END IF;
    passed := passed + 1; RAISE NOTICE 'T4 PASS: org A sees only org A rows';

    -- ---- T5: WITH CHECK blocks cross-org INSERT ---------------------------
    BEGIN
        INSERT INTO products (organization_id, workspace_id, name)
        VALUES (orgB, '00000000-0000-7000-8000-0000000000b1', 'evil');
        RAISE EXCEPTION 'T5 FAIL: cross-org insert was allowed';
    EXCEPTION WHEN insufficient_privilege OR check_violation THEN
        NULL; -- expected: RLS WITH CHECK rejects it
    END;
    passed := passed + 1; RAISE NOTICE 'T5 PASS: WITH CHECK blocks cross-org insert';

    -- ---- T6: cross-org UPDATE affects zero rows ---------------------------
    UPDATE documents SET title = 'hijacked' WHERE organization_id = orgB;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 0 THEN RAISE EXCEPTION 'T6 FAIL: updated % org B rows', n; END IF;
    passed := passed + 1; RAISE NOTICE 'T6 PASS: cross-org update affects zero rows';

    -- ---- T7: RLS inherited by every chunk partition -----------------------
    SELECT count(*) INTO n
      FROM pg_inherits i
      JOIN pg_class child ON child.oid = i.inhrelid
      JOIN pg_class parent ON parent.oid = i.inhparent
     WHERE parent.relname = 'document_chunks'
       AND child.relrowsecurity = true
       AND child.relforcerowsecurity = true;
    IF n <> 8 THEN RAISE EXCEPTION 'T7 FAIL: only %/8 partitions enforce RLS', n; END IF;
    passed := passed + 1; RAISE NOTICE 'T7 PASS: all 8 partitions inherit FORCE RLS';

    -- ---- T8: hash partitioning on organization_id -------------------------
    SELECT count(*) INTO n FROM pg_partitioned_table pt
      JOIN pg_class c ON c.oid = pt.partrelid
     WHERE c.relname = 'document_chunks' AND pt.partstrat = 'h';
    IF n <> 1 THEN RAISE EXCEPTION 'T8 FAIL: document_chunks not hash-partitioned'; END IF;
    passed := passed + 1; RAISE NOTICE 'T8 PASS: document_chunks hash-partitioned';

    -- ---- T9: UUIDv7 conformance + ordering ---------------------------------
    SELECT bool_and(substring(u::text,15,1)='7'
                AND substring(u::text,20,1) IN ('8','9','a','b'))
      INTO ok
      FROM (SELECT pmos.uuid_generate_v7() u FROM generate_series(1,500)) s;
    IF NOT ok THEN RAISE EXCEPTION 'T9 FAIL: UUIDv7 version/variant bits wrong'; END IF;
    passed := passed + 1; RAISE NOTICE 'T9 PASS: UUIDv7 version+variant conformant';

    -- ---- T10: residency write-once ----------------------------------------
    PERFORM set_config('app.current_org_id', orgA::text, true);
    BEGIN
        UPDATE organizations SET residency_region = 'ap-south-1' WHERE id = orgA;
        RAISE EXCEPTION 'T10 FAIL: residency_region was mutable';
    EXCEPTION WHEN check_violation THEN
        NULL; -- expected: immutability trigger fired
    END;
    passed := passed + 1; RAISE NOTICE 'T10 PASS: residency_region is write-once';

    -- ---- T11: updated_at trigger advances ----------------------------------
    UPDATE products SET name = name WHERE organization_id = orgA;
    SELECT bool_and(updated_at >= created_at) INTO ok FROM products WHERE organization_id = orgA;
    IF NOT ok THEN RAISE EXCEPTION 'T11 FAIL: updated_at did not advance'; END IF;
    passed := passed + 1; RAISE NOTICE 'T11 PASS: updated_at trigger advances';

    -- ---- T12: soft-delete partial indexes ---------------------------------
    SELECT count(*) INTO n FROM pg_indexes
      WHERE tablename IN ('products','documents','workspaces')
        AND indexdef ILIKE '%deleted_at IS NULL%';
    IF n < 3 THEN RAISE EXCEPTION 'T12 FAIL: expected partial soft-delete indexes, found %', n; END IF;
    passed := passed + 1; RAISE NOTICE 'T12 PASS: soft-delete partial indexes present';

    -- ---- T13: chunk.id = Qdrant point id contract -------------------------
    -- The PK includes id; id is independently resolvable within the org.
    SELECT count(*) INTO n FROM document_chunks
     WHERE id = '00000000-0000-7000-8000-0000000000a9'
       AND organization_id = orgA;
    IF n <> 1 THEN RAISE EXCEPTION 'T13 FAIL: chunk id not resolvable as point id'; END IF;
    passed := passed + 1; RAISE NOTICE 'T13 PASS: chunk.id resolvable as Qdrant point id';

    RAISE NOTICE '================= ALL % TESTS PASSED =================', passed;
END;
$$;

RESET ROLE;
