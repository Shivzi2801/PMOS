import { SecretEnvelope } from '../service/SecretCrypto';

/**
 * Tenant-scoped secret model.
 *
 * Secrets are addressed by (tenantId, connectionId, name). `name` lets a single
 * connection hold multiple secret materials (e.g. "oauth" + "webhook_signing").
 * Every row is versioned: rotation writes a new version and marks prior
 * versions superseded, never overwriting in place. This gives a rollback path
 * and a clean audit trail.
 *
 * RLS: every query MUST be constrained by tenantId. The Prisma store sets the
 * `app.tenant_id` session GUC so Postgres RLS policies enforce isolation even
 * if application code has a bug.
 */

export type SecretStatus = 'ACTIVE' | 'SUPERSEDED' | 'REVOKED';

export interface StoredSecret {
  readonly id: string;
  readonly tenantId: string;
  readonly connectionId: string;
  readonly name: string;
  readonly version: number;
  readonly status: SecretStatus;
  readonly envelope: SecretEnvelope;
  readonly createdAt: Date;
  readonly rotatedAt?: Date;
  /** Optional expiry hint for credentials (epoch ms), drives proactive rotation. */
  readonly expiresAt?: number;
}

export interface SecretRef {
  readonly tenantId: string;
  readonly connectionId: string;
  readonly name: string;
}

/**
 * Persistence port for secrets. The Prisma implementation lives in the
 * persistence layer; tests use an in-memory fake.
 */
export interface SecretStore {
  /** Return the ACTIVE version for a ref, or null. */
  getActive(ref: SecretRef): Promise<StoredSecret | null>;

  /** Return a specific version. */
  getVersion(ref: SecretRef, version: number): Promise<StoredSecret | null>;

  /** List all versions for a ref, newest first. */
  listVersions(ref: SecretRef): Promise<readonly StoredSecret[]>;

  /**
   * Insert a new version as ACTIVE and mark any prior ACTIVE version
   * SUPERSEDED, atomically. Returns the newly created secret.
   */
  putNewVersion(input: {
    ref: SecretRef;
    envelope: SecretEnvelope;
    expiresAt?: number;
  }): Promise<StoredSecret>;

  /** Mark all versions for a ref REVOKED (used on disconnect). */
  revokeAll(ref: SecretRef): Promise<number>;

  /** Hard-delete all versions for a connection (used on connection deletion). */
  deleteAll(ref: Omit<SecretRef, 'name'>): Promise<number>;

  /** Run `fn` while holding a row lock on the secret ref to serialize rotation. */
  withLock<T>(ref: SecretRef, fn: () => Promise<T>): Promise<T>;
}
