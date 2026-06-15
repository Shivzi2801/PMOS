/**
 * Secret auditing.
 *
 * Every secret operation (write, read, rotate, revoke, decrypt-for-use) emits
 * an audit record onto the audit rail. Audit records NEVER contain secret
 * material — only metadata: who, what ref, which version, outcome, and a
 * monotonically increasing access sequence per ref for tamper-evidence.
 *
 * The audit emitter is a port so it can target the Wave-0 audit rail in
 * production and an array in tests.
 */

export type SecretAuditAction =
  | 'WRITE'
  | 'READ'
  | 'ROTATE'
  | 'REVOKE'
  | 'DELETE'
  | 'DECRYPT_FOR_USE';

export type SecretAuditOutcome = 'SUCCESS' | 'FAILURE';

export interface SecretAuditEvent {
  readonly tenantId: string;
  readonly connectionId: string;
  readonly secretName: string;
  readonly version?: number;
  readonly action: SecretAuditAction;
  readonly outcome: SecretAuditOutcome;
  readonly actor: string;
  readonly correlationId: string;
  /** KEK id involved, useful for key-rotation audits. Never the DEK. */
  readonly keyId?: string;
  readonly reason?: string;
  readonly at: string; // ISO-8601
}

export interface SecretAuditSink {
  record(event: SecretAuditEvent): Promise<void>;
}

/** In-memory sink for tests. */
export class InMemorySecretAuditSink implements SecretAuditSink {
  public readonly events: SecretAuditEvent[] = [];
  async record(event: SecretAuditEvent): Promise<void> {
    this.events.push(event);
  }
}
