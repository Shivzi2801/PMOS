# @pmos/connectors — F-08 Connector SDK Foundation

Wave 1 · Slice 1. Production-grade connector SDK with a Zendesk reference
connector and per-tenant KMS-enveloped secret storage.

## What's here

- **SDK** (`sdk/`) — `Connector` interface composed of Auth / Sync / Health /
  Lifecycle, a typed error taxonomy, a deterministic retry engine, the
  connection lifecycle state machine, and the sync orchestrator.
- **Secrets** (`secrets/`) — `SecretService` over AES-256-GCM envelope
  encryption, pluggable `KmsProvider` (AWS + Local), versioned `SecretStore`
  (Prisma + RLS), and append-only auditing.
- **Zendesk** (`zendesk/`) — OAuth2 + PKCE authenticator, cursor-based
  incremental sync, HMAC webhook verification with replay protection, health
  check, fetch HTTP client.
- **Events** (`events/`) — `ConnectorConnected`, `ConnectorDisconnected`,
  `SyncStarted`, `SyncCompleted`, `SyncFailed` contracts + factory.
- **Persistence** (`../prisma/`) — Prisma models + a SQL migration that creates
  the tables and applies forced Row-Level Security.
- **Tests** (`tests/`) — 50 tests across unit, integration, and a reusable
  connector contract suite.
- **Docs** (`docs/`) — architecture, sequence diagrams, failure handling.

## Run

```bash
npm install
npm run typecheck   # strict tsc, no emit
npm test            # vitest, 50 tests
npm run build       # emit dist/
```

## Wiring

See `composition.ts` for the production dependency graph. The only dev/prod
differences are the KMS provider and HTTP client; everything else is identical.

## Guarantees

- **Multi-tenant safe / RLS compliant** — every table forces RLS keyed on
  `app.tenant_id`; cross-tenant secret access also fails cryptographically.
- **At-least-once sync with safe resume** — records persisted before cursor
  advance.
- **No plaintext secrets at rest** — only KMS-enveloped ciphertext; DEKs and
  plaintext zeroed after use.
- **Typed failure handling** — error category alone drives retry/refresh/fail.
