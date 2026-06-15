# F-08 Connector SDK Foundation — Architecture

**Wave 1 · Slice 1 · Reference connector: Zendesk**

This document describes the connector SDK, the per-tenant KMS-enveloped secret
layer, the Zendesk reference connector, persistence, events, and failure
handling.

## 1. Layering

```
┌──────────────────────────────────────────────────────────────────────┐
│  Platform (Wave-0 rails: tenant, schema, events, audit, edge)          │
└───────────────▲──────────────────────────────────────▲────────────────┘
                │ events                                │ audit
┌───────────────┴───────────────┐      ┌────────────────┴───────────────┐
│  Connector SDK (sdk/)          │      │  Secret Management (secrets/)   │
│  - Connector interface         │      │  - SecretService (facade)       │
│  - Auth / Sync / Health        │      │  - SecretCrypto (AES-256-GCM)   │
│  - Lifecycle state machine     │      │  - KmsProvider (AWS / Local)    │
│  - Retry engine                │      │  - SecretStore (Prisma + RLS)   │
│  - SyncOrchestrator            │      │  - SecretAudit sink             │
│  - Error taxonomy              │      └─────────────────────────────────┘
└───────────────▲────────────────┘
                │ implements
┌───────────────┴────────────────┐
│  Zendesk connector (zendesk/)   │
│  - OAuth2 + PKCE authenticator  │
│  - Incremental cursor sync      │
│  - Webhook HMAC verifier        │
│  - Health check                 │
│  - Fetch HTTP client            │
└─────────────────────────────────┘
```

The SDK defines contracts; the Zendesk package implements them; the secret
layer is consumed by both through narrow ports. Nothing in the connector
touches the database or the network directly — `HttpClient`, `CredentialProvider`,
and the store ports are all injected, which is what makes the connector fully
unit-testable without infrastructure.

## 2. Multi-tenancy & RLS

Every tenant-scoped table (`connector`, `connector_secret`,
`connector_sync_state`, `connector_sync_run`, `connector_secret_audit`) has
`ROW LEVEL SECURITY` **enabled and forced**. Policies compare `tenantId` to the
transaction-local GUC `app.tenant_id`, set by `withTenant()` via
`set_config('app.tenant_id', $tenant, true)`. Because the setting is
transaction-local it cannot leak across pooled connections, and because RLS is
*forced* even the table owner is constrained. If the GUC is unset, the policy
predicate evaluates against `NULL` and **fails closed** — zero rows.

The `TenantContext` object is threaded through every SDK operation and is the
single source of `tenantId`. A connector that tried to read another tenant's
data would be blocked twice: once by application scoping, once by RLS.

## 3. Secret management — envelope encryption

```
write(plaintext)
   │
   ▼
KMS.generateDataKey(tenantKEK) ──▶ { plaintextDEK, wrappedDEK }
   │
   ├─ AES-256-GCM encrypt(plaintext, plaintextDEK) ──▶ ciphertext, iv, authTag
   ├─ zero(plaintextDEK)                      (DEK never persisted in clear)
   ▼
persist { ciphertext, iv, authTag, wrappedDEK, keyId, version=n+1, ACTIVE }
        (prior ACTIVE → SUPERSEDED, atomic, single-ACTIVE partial unique index)
```

- **Per-tenant KEK**: addressed by `alias/pmos/tenant/<tenantId>` in AWS KMS.
  Cross-tenant decryption is cryptographically impossible — a DEK wrapped under
  tenant A's KEK cannot be unwrapped with tenant B's.
- **Versioned, never overwritten**: rotation writes a new version and supersedes
  the old. Gives rollback and a clean audit trail. At most one ACTIVE version is
  enforced by a partial unique index.
- **Plaintext lifetime**: minimized. `useSecret()` scopes plaintext to a
  callback and zeroes the buffer afterward. DEKs are zeroed immediately after
  each crypto operation.
- **Auditing**: every WRITE / READ / ROTATE / REVOKE / DELETE / DECRYPT_FOR_USE
  emits an append-only audit record (no secret material — only metadata, keyId,
  outcome).
- **Rotation strategy**: `shouldRotate()` returns true when the active
  credential is within the lead window (default 5 min) of expiry, so the
  scheduler refreshes proactively before in-flight failures.

## 4. Lifecycle state machine

```
        CONNECT                 DISCONNECT
PENDING ─────────▶ ACTIVE ───────────────▶ DISCONNECTED
   │                 │  ▲                        ▲
   │ FAIL       DEGRADE│  │RECOVER                │ REACTIVATE
   ▼                 ▼  │                         │
 ERROR ◀───────── DEGRADED                        │
   │  REACTIVATE                                  │
   └──────────────────────────────────────────────┘
```

Only the `ConnectionLifecycle` manager mutates state, under a row lock, and it
emits `ConnectorConnected` on first entry to ACTIVE and `ConnectorDisconnected`
on entry to DISCONNECTED. Illegal transitions raise `ConfigurationError`.

## 5. Incremental sync

The `SyncOrchestrator` drives a stream and guarantees **at-least-once delivery
with safe resume**:

1. Open a run, emit `SyncStarted` (with the resume cursor).
2. For each batch: **persist records first**, then advance the cursor. A crash
   between the two re-delivers at most one batch on resume.
3. Emit `SyncCompleted` (success) or `SyncFailed` (with the cursor reached, so
   the next run resumes from there).

Zendesk uses the cursor-based Incremental Export API; the persisted cursor is a
base64url JSON blob (`{ cursor }` or `{ startTime }`) so we can resume by opaque
Zendesk cursor or epoch.

## 6. Failure handling

See `FAILURE-HANDLING.md` for the full matrix. In short, every provider error is
normalized into the SDK taxonomy whose `retryable` + `category` fields drive the
retry engine:

| Condition            | Error type          | Retryable | Behavior                          |
|----------------------|---------------------|-----------|-----------------------------------|
| 429 + Retry-After    | RateLimitError      | yes       | wait `Retry-After`, then retry    |
| 5xx / network        | TransientError      | yes       | exponential backoff + jitter      |
| 401                  | AuthenticationError | yes       | refresh credential, then retry    |
| 403                  | AuthorizationError  | no        | surface to tenant (scope problem) |
| 422 / bad input      | ValidationError     | no        | fail fast                         |
| other 4xx            | PermanentError      | no        | fail fast                         |
| bad payload          | DataIntegrityError  | no        | fail, alert                       |

## 7. Files

```
backend/connectors/
├── sdk/
│   ├── interfaces/   types, Connector, Auth, Sync, Health, Lifecycle
│   ├── contracts/    ConnectionLifecycle, SyncOrchestrator
│   ├── errors/       ConnectorError taxonomy
│   └── retry/        backoff engine
├── secrets/
│   ├── kms/          KmsProvider, AwsKmsProvider, LocalKmsProvider
│   ├── model/        SecretModel + SecretStore port
│   └── service/      SecretCrypto, SecretService, SecretAudit, Prisma stores
├── zendesk/          authenticator, sync, webhook, health, http, connector
├── events/           ConnectorEvents (5 contracts + factory)
└── tests/            unit, integration, contract
backend/prisma/
├── connectors.prisma
└── migrations/20260615000001_connector_sdk_foundation/migration.sql
```
