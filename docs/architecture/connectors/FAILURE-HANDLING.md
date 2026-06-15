# Failure Handling — F-08 Connector SDK

This is the authoritative reference for how the connector stack behaves under
every class of failure. The design principle: **errors are typed data, and the
type alone determines behavior.** No connector writes its own retry loop or
ad-hoc status-code handling — it throws a `ConnectorError` subclass and the SDK
does the rest.

## 1. Error taxonomy → behavior

| Category         | Subclass             | `retryable` | Driven behavior                                   |
|------------------|----------------------|-------------|---------------------------------------------------|
| RATE_LIMITED     | `RateLimitError`     | true        | Wait `retryAfterMs` (from Retry-After), then retry |
| TRANSIENT        | `TransientError`     | true        | Exponential backoff + jitter (capped)             |
| AUTHENTICATION   | `AuthenticationError`| true        | Refresh credential, then retry                    |
| UNKNOWN          | `ConnectorError`     | true        | Treated as transient, retried cautiously          |
| AUTHORIZATION    | `AuthorizationError` | false       | Surface to tenant; scope/permission fix needed    |
| VALIDATION       | `ValidationError`    | false       | Fail fast; caller bug or bad provider input       |
| PERMANENT        | `PermanentError`     | false       | Fail fast; stable provider rejection              |
| CONFIGURATION    | `ConfigurationError` | false       | Fail; our state/config is wrong                   |
| DATA_INTEGRITY   | `DataIntegrityError` | false       | Fail + alert; payload violated our contract       |

`toConnectorError()` normalizes any thrown value at SDK boundaries so downstream
code never sees a raw `unknown`.

## 2. HTTP status mapping (Zendesk)

`mapResponseToError` converts provider responses:

- `2xx` → `null` (success)
- `429` → `RateLimitError(retryAfterMs)` parsed from `Retry-After` (seconds or
  HTTP-date; default 1s)
- `401` → `AuthenticationError` (triggers refresh-then-retry)
- `403` → `AuthorizationError` (non-retryable)
- `422` → `ValidationError`
- `5xx` → `TransientError`
- other `4xx` → `PermanentError`

## 3. Retry engine

`executeWithRetry` (sdk/retry):

- Exponential backoff: `base · multiplier^(attempt-1)`, capped at `maxDelayMs`.
- Jitter band `[1−j, 1+j]` to avoid thundering herds.
- Explicit `retryAfterMs` on the error always overrides computed delay.
- Only categories in the retryable set are retried; everything else throws
  immediately.
- `maxAttempts` includes the first try. Defaults: 5 attempts, 250 ms base, 30 s
  cap, 0.2 jitter, ×2.
- `sleep`/`random` are injectable → deterministic, fast unit tests (virtual
  time, no real waiting).
- Honors `AbortSignal` between attempts.

## 4. Sync failure semantics

The orchestrator persists records **before** advancing the cursor. Consequences:

- **Crash / sink failure mid-stream**: cursor reflects only durably-written
  batches. On resume, at most one batch is re-delivered. Sinks must be
  idempotent on `(stream, id)` (upsert) — which the persistence design assumes.
- **Provider failure mid-stream**: `SyncFailed(atCursor)` is emitted; the run
  row records `errorCategory` + `errorMessage`; the next scheduled run resumes
  from `atCursor`.
- **Partial batch**: never persisted partially — `Sink.write` is the atomic unit
  from the orchestrator's perspective; cursor advances only on its success.

## 5. Credential failures

- **Expiring credential**: `SecretService.shouldRotate` flags it within the lead
  window; the scheduler calls `Authenticator.refresh` and `SecretService.rotate`
  proactively.
- **401 mid-sync**: surfaces as `AuthenticationError` (retryable); the auth path
  refreshes and the engine retries with the new credential.
- **Revoked / no refresh token**: `refresh` throws a non-actionable
  `AuthenticationError`; the connection is moved to `ERROR` and the tenant is
  prompted to re-authorize. The lifecycle emits no `ConnectorConnected` until
  re-auth succeeds.

## 6. Secret-layer failures

- **KMS unavailable**: encrypt/decrypt throw; `SecretService` records a FAILURE
  audit event and propagates. Callers treat it as transient (KMS outages are
  transient) but secrets are never written in plaintext as a fallback.
- **Tampered ciphertext**: AES-GCM auth-tag verification fails on decrypt →
  throws. Never returns corrupted plaintext.
- **Tenant/KEK mismatch**: a DEK wrapped under tenant A cannot be unwrapped with
  tenant B's KEK — KMS rejects it. Cross-tenant access fails cryptographically,
  independent of application logic and RLS.
- **Concurrent rotation**: serialized by a per-ref advisory lock + the
  single-ACTIVE partial unique index. A losing racer either supersedes cleanly
  or is rejected by the DB constraint; no two ACTIVE versions can coexist.

## 7. Webhook failures

- **Bad signature**: constant-time HMAC compare fails → `ValidationError`,
  request rejected (no record ingested).
- **Replayed delivery**: timestamp outside the tolerance window (default 5 min)
  → `ValidationError`.
- **Malformed body (post-signature)**: `DataIntegrityError`.
- **Defense in depth**: a verified webhook triggers a *targeted incremental
  sync* rather than trusting the payload as source of truth, so a forged-but-
  somehow-valid payload still can't inject unverified data.

## 8. RLS / isolation failures

- **Missing `app.tenant_id`**: RLS predicate compares against `NULL` → zero rows
  visible and writes rejected. Fails closed, never open.
- **Wrong tenant in context**: even if application code passed the wrong
  `tenantId`, RLS blocks reads/writes to rows owned by a different tenant.
- **Append-only audit**: UPDATE/DELETE policies on `connector_secret_audit`
  evaluate to `false` for the app role — audit history is immutable.
