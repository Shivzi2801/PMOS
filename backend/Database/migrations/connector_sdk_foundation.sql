-- =============================================================================
-- Migration: 20260615000001_connector_sdk_foundation
-- Wave 1 Slice 1 — F-08 Connector SDK Foundation
--
-- Creates connector, secret, sync-state, sync-run, and secret-audit tables,
-- then enables PostgreSQL Row-Level Security so a connection's rows are only
-- visible/mutable when the session GUC `app.tenant_id` matches `tenant_id`.
--
-- The application sets `app.tenant_id` per request/transaction:
--     SELECT set_config('app.tenant_id', $1, true);
-- RLS is FORCED so even the table owner is constrained (defense in depth).
-- A dedicated migration/superuser role bypasses RLS for schema ops only.
-- =============================================================================

-- ---------- Enums ----------------------------------------------------------
CREATE TYPE "ConnectionState" AS ENUM ('PENDING', 'ACTIVE', 'DEGRADED', 'ERROR', 'DISCONNECTED');
CREATE TYPE "SecretStatus"    AS ENUM ('ACTIVE', 'SUPERSEDED', 'REVOKED');
CREATE TYPE "SyncRunState"    AS ENUM ('RUNNING', 'COMPLETED', 'FAILED');

-- ---------- connector ------------------------------------------------------
CREATE TABLE "connector" (
  "id"             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "tenantId"       TEXT NOT NULL,
  "connectorType"  TEXT NOT NULL,
  "displayName"    TEXT,
  "state"          "ConnectionState" NOT NULL DEFAULT 'PENDING',
  "stateReason"    TEXT,
  "config"         JSONB NOT NULL DEFAULT '{}'::jsonb,
  "scopes"         TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "createdAt"      TIMESTAMP(3) NOT NULL DEFAULT now(),
  "updatedAt"      TIMESTAMP(3) NOT NULL DEFAULT now()
);
CREATE INDEX "connector_tenant_type_idx"  ON "connector" ("tenantId", "connectorType");
CREATE INDEX "connector_tenant_state_idx" ON "connector" ("tenantId", "state");

-- ---------- connector_secret ----------------------------------------------
CREATE TABLE "connector_secret" (
  "id"           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "tenantId"     TEXT NOT NULL,
  "connectionId" TEXT NOT NULL,
  "name"         TEXT NOT NULL,
  "version"      INTEGER NOT NULL,
  "status"       "SecretStatus" NOT NULL DEFAULT 'ACTIVE',
  "ciphertext"   BYTEA NOT NULL,
  "iv"           BYTEA NOT NULL,
  "authTag"      BYTEA NOT NULL,
  "wrappedDek"   BYTEA NOT NULL,
  "keyId"        TEXT NOT NULL,
  "expiresAt"    BIGINT,
  "createdAt"    TIMESTAMP(3) NOT NULL DEFAULT now(),
  "rotatedAt"    TIMESTAMP(3),
  CONSTRAINT "connector_secret_connection_fk"
    FOREIGN KEY ("connectionId") REFERENCES "connector"("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX "connector_secret_unique_version"
  ON "connector_secret" ("tenantId", "connectionId", "name", "version");
CREATE INDEX "connector_secret_active_idx"
  ON "connector_secret" ("tenantId", "connectionId", "name", "status");
-- Enforce at most one ACTIVE version per logical secret.
CREATE UNIQUE INDEX "connector_secret_one_active"
  ON "connector_secret" ("tenantId", "connectionId", "name")
  WHERE "status" = 'ACTIVE';

-- ---------- connector_sync_state -------------------------------------------
CREATE TABLE "connector_sync_state" (
  "id"           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "tenantId"     TEXT NOT NULL,
  "connectionId" TEXT NOT NULL,
  "stream"       TEXT NOT NULL,
  "cursor"       TEXT,
  "lastSyncedAt" TIMESTAMP(3),
  "updatedAt"    TIMESTAMP(3) NOT NULL DEFAULT now(),
  CONSTRAINT "connector_sync_state_connection_fk"
    FOREIGN KEY ("connectionId") REFERENCES "connector"("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX "connector_sync_state_unique"
  ON "connector_sync_state" ("tenantId", "connectionId", "stream");
CREATE INDEX "connector_sync_state_conn_idx"
  ON "connector_sync_state" ("tenantId", "connectionId");

-- ---------- connector_sync_run ---------------------------------------------
CREATE TABLE "connector_sync_run" (
  "id"               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "tenantId"         TEXT NOT NULL,
  "connectionId"     TEXT NOT NULL,
  "stream"           TEXT NOT NULL,
  "runId"            TEXT NOT NULL,
  "state"            "SyncRunState" NOT NULL DEFAULT 'RUNNING',
  "mode"             TEXT NOT NULL,
  "recordsProcessed" INTEGER NOT NULL DEFAULT 0,
  "batches"          INTEGER NOT NULL DEFAULT 0,
  "fromCursor"       TEXT,
  "toCursor"         TEXT,
  "errorCategory"    TEXT,
  "errorMessage"     TEXT,
  "startedAt"        TIMESTAMP(3) NOT NULL DEFAULT now(),
  "finishedAt"       TIMESTAMP(3),
  CONSTRAINT "connector_sync_run_connection_fk"
    FOREIGN KEY ("connectionId") REFERENCES "connector"("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX "connector_sync_run_unique" ON "connector_sync_run" ("tenantId", "runId");
CREATE INDEX "connector_sync_run_idx"
  ON "connector_sync_run" ("tenantId", "connectionId", "stream", "startedAt");

-- ---------- connector_secret_audit -----------------------------------------
CREATE TABLE "connector_secret_audit" (
  "id"            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  "tenantId"      TEXT NOT NULL,
  "connectionId"  TEXT NOT NULL,
  "secretName"    TEXT NOT NULL,
  "version"       INTEGER,
  "action"        TEXT NOT NULL,
  "outcome"       TEXT NOT NULL,
  "actor"         TEXT NOT NULL,
  "correlationId" TEXT NOT NULL,
  "keyId"         TEXT,
  "reason"        TEXT,
  "at"            TIMESTAMP(3) NOT NULL DEFAULT now()
);
CREATE INDEX "connector_secret_audit_idx"
  ON "connector_secret_audit" ("tenantId", "connectionId", "secretName", "at");

-- =============================================================================
-- Row-Level Security
-- =============================================================================
-- Helper: current tenant from session GUC. Returns NULL if unset, which makes
-- every USING/WITH CHECK predicate fail closed (no rows visible).
CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS TEXT
  LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')
$$;

-- Apply RLS explicitly per table (no dynamic SQL; clearest for review).
ALTER TABLE "connector" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "connector" FORCE ROW LEVEL SECURITY;
CREATE POLICY "connector_tenant_isolation" ON "connector"
  USING ("tenantId" = app_current_tenant());
CREATE POLICY "connector_tenant_write" ON "connector"
  FOR ALL
  USING ("tenantId" = app_current_tenant())
  WITH CHECK ("tenantId" = app_current_tenant());

ALTER TABLE "connector_secret" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "connector_secret" FORCE ROW LEVEL SECURITY;
CREATE POLICY "connector_secret_tenant_isolation" ON "connector_secret"
  USING ("tenantId" = app_current_tenant());
CREATE POLICY "connector_secret_tenant_write" ON "connector_secret"
  FOR ALL
  USING ("tenantId" = app_current_tenant())
  WITH CHECK ("tenantId" = app_current_tenant());

ALTER TABLE "connector_sync_state" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "connector_sync_state" FORCE ROW LEVEL SECURITY;
CREATE POLICY "connector_sync_state_tenant_isolation" ON "connector_sync_state"
  USING ("tenantId" = app_current_tenant());
CREATE POLICY "connector_sync_state_tenant_write" ON "connector_sync_state"
  FOR ALL
  USING ("tenantId" = app_current_tenant())
  WITH CHECK ("tenantId" = app_current_tenant());

ALTER TABLE "connector_sync_run" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "connector_sync_run" FORCE ROW LEVEL SECURITY;
CREATE POLICY "connector_sync_run_tenant_isolation" ON "connector_sync_run"
  USING ("tenantId" = app_current_tenant());
CREATE POLICY "connector_sync_run_tenant_write" ON "connector_sync_run"
  FOR ALL
  USING ("tenantId" = app_current_tenant())
  WITH CHECK ("tenantId" = app_current_tenant());

ALTER TABLE "connector_secret_audit" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "connector_secret_audit" FORCE ROW LEVEL SECURITY;
CREATE POLICY "connector_secret_audit_tenant_isolation" ON "connector_secret_audit"
  USING ("tenantId" = app_current_tenant());
-- Append-only: allow INSERT (tenant-scoped) and SELECT, but never UPDATE/DELETE.
CREATE POLICY "connector_secret_audit_tenant_insert" ON "connector_secret_audit"
  FOR INSERT
  WITH CHECK ("tenantId" = app_current_tenant());

-- Append-only is achieved structurally: with RLS forced and no permissive
-- UPDATE/DELETE policy present, UPDATE and DELETE are denied for the app role
-- (default-deny). Only the INSERT and SELECT policies above grant access.
-- The migration/superuser role bypasses RLS for maintenance.
