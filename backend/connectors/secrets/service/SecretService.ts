import { Clock, systemClock } from '../../sdk/interfaces/types';
import { ConfigurationError } from '../../sdk/errors/ConnectorError';
import { SecretCrypto } from './SecretCrypto';
import { SecretRef, SecretStore, StoredSecret } from '../model/SecretModel';
import {
  SecretAuditAction,
  SecretAuditOutcome,
  SecretAuditSink,
} from './SecretAudit';

/**
 * SecretService
 *
 * The single entry point for all secret operations. Responsibilities:
 *   - Envelope encrypt on write, decrypt on read (delegated to SecretCrypto).
 *   - Versioned rotation: writes always create a new version; rotation is
 *     serialized per ref via the store's row lock to prevent lost updates and
 *     to make refresh-token rotation safe under concurrency.
 *   - Auditing: every operation emits an audit event with outcome.
 *   - Proactive rotation: `shouldRotate` surfaces credentials nearing expiry so
 *     the scheduler can refresh them before they fail in-flight.
 *
 * Secret plaintext is returned as a Buffer and the caller is responsible for
 * using it transiently. `useSecret` is the preferred API: it scopes plaintext
 * to a callback and zeroes it afterward.
 */

export interface AuditContext {
  readonly actor: string;
  readonly correlationId: string;
}

/** Proactive rotation: rotate when within this window of expiry. */
export const DEFAULT_ROTATION_LEAD_MS = 5 * 60 * 1000; // 5 minutes

export class SecretService {
  constructor(
    private readonly store: SecretStore,
    private readonly crypto: SecretCrypto,
    private readonly audit: SecretAuditSink,
    private readonly clock: Clock = systemClock,
    private readonly rotationLeadMs: number = DEFAULT_ROTATION_LEAD_MS,
  ) {}

  /**
   * Write a brand-new secret or rotate an existing one. Encrypts plaintext,
   * stores a new ACTIVE version, and supersedes the prior version atomically.
   */
  async write(
    ref: SecretRef,
    plaintext: Buffer,
    audit: AuditContext,
    opts: { expiresAt?: number; action?: SecretAuditAction } = {},
  ): Promise<StoredSecret> {
    return this.store.withLock(ref, async () => {
      try {
        const envelope = await this.crypto.encrypt(ref.tenantId, plaintext);
        const stored = await this.store.putNewVersion({
          ref,
          envelope,
          expiresAt: opts.expiresAt,
        });
        await this.emit(ref, stored.version, opts.action ?? 'WRITE', 'SUCCESS', audit, envelope.keyId);
        return stored;
      } catch (err) {
        await this.emit(ref, undefined, opts.action ?? 'WRITE', 'FAILURE', audit, undefined, String(err));
        throw err;
      } finally {
        plaintext.fill(0);
      }
    });
  }

  /** Convenience wrapper for rotation semantics + audit action. */
  async rotate(
    ref: SecretRef,
    newPlaintext: Buffer,
    audit: AuditContext,
    opts: { expiresAt?: number } = {},
  ): Promise<StoredSecret> {
    return this.write(ref, newPlaintext, audit, { ...opts, action: 'ROTATE' });
  }

  /**
   * Decrypt the ACTIVE secret and pass the plaintext to `fn`. The plaintext is
   * zeroed after `fn` resolves/rejects. This is the safest read API and should
   * be preferred over `read`.
   */
  async useSecret<T>(
    ref: SecretRef,
    audit: AuditContext,
    fn: (plaintext: Buffer) => Promise<T>,
  ): Promise<T> {
    const active = await this.store.getActive(ref);
    if (!active) {
      await this.emit(ref, undefined, 'READ', 'FAILURE', audit, undefined, 'not_found');
      throw new ConfigurationError('No active secret for ref', {
        tenantId: ref.tenantId,
        connectionId: ref.connectionId,
        detail: { name: ref.name },
      });
    }
    let plaintext: Buffer | undefined;
    try {
      plaintext = await this.crypto.decrypt(active.envelope);
      await this.emit(ref, active.version, 'DECRYPT_FOR_USE', 'SUCCESS', audit, active.envelope.keyId);
      return await fn(plaintext);
    } catch (err) {
      await this.emit(ref, active.version, 'DECRYPT_FOR_USE', 'FAILURE', audit, active.envelope.keyId, String(err));
      throw err;
    } finally {
      if (plaintext) plaintext.fill(0);
    }
  }

  /**
   * Read and return the ACTIVE secret plaintext. Caller MUST zero it. Prefer
   * `useSecret` unless the lifetime genuinely outlives a callback.
   */
  async read(ref: SecretRef, audit: AuditContext): Promise<Buffer> {
    const active = await this.store.getActive(ref);
    if (!active) {
      await this.emit(ref, undefined, 'READ', 'FAILURE', audit, undefined, 'not_found');
      throw new ConfigurationError('No active secret for ref', {
        tenantId: ref.tenantId,
        connectionId: ref.connectionId,
        detail: { name: ref.name },
      });
    }
    const plaintext = await this.crypto.decrypt(active.envelope);
    await this.emit(ref, active.version, 'READ', 'SUCCESS', audit, active.envelope.keyId);
    return plaintext;
  }

  /** Revoke (logically) all versions for a ref. Used on disconnect. */
  async revoke(ref: SecretRef, audit: AuditContext, reason: string): Promise<void> {
    const n = await this.store.revokeAll(ref);
    await this.emit(ref, undefined, 'REVOKE', 'SUCCESS', audit, undefined, `${reason} (versions=${n})`);
  }

  /** Hard-delete all secrets for a connection. Used on connection deletion. */
  async deleteConnectionSecrets(
    tenantId: string,
    connectionId: string,
    audit: AuditContext,
  ): Promise<void> {
    const n = await this.store.deleteAll({ tenantId, connectionId });
    await this.emit(
      { tenantId, connectionId, name: '*' },
      undefined,
      'DELETE',
      'SUCCESS',
      audit,
      undefined,
      `versions=${n}`,
    );
  }

  /**
   * Proactive rotation check: returns true when the active secret is expired or
   * within the rotation lead window. The scheduler uses this to trigger a
   * credential refresh ahead of failure.
   */
  async shouldRotate(ref: SecretRef): Promise<boolean> {
    const active = await this.store.getActive(ref);
    if (!active || active.expiresAt === undefined) return false;
    const now = this.clock.now().getTime();
    return active.expiresAt - now <= this.rotationLeadMs;
  }

  private async emit(
    ref: SecretRef,
    version: number | undefined,
    action: SecretAuditAction,
    outcome: SecretAuditOutcome,
    audit: AuditContext,
    keyId?: string,
    reason?: string,
  ): Promise<void> {
    await this.audit.record({
      tenantId: ref.tenantId,
      connectionId: ref.connectionId,
      secretName: ref.name,
      version,
      action,
      outcome,
      actor: audit.actor,
      correlationId: audit.correlationId,
      keyId,
      reason,
      at: this.clock.now().toISOString(),
    });
  }
}
