/**
 * Shared primitive types used across every connector interface.
 *
 * The `TenantContext` is the single most important object in the SDK: it is
 * threaded through every operation and is the basis for RLS enforcement,
 * secret scoping, and audit attribution. A connector must NEVER perform work
 * without a TenantContext, and must NEVER read or write data for a tenant
 * other than `tenantId`.
 */

export type Brand<T, B extends string> = T & { readonly __brand: B };

export type TenantId = Brand<string, 'TenantId'>;
export type ConnectionId = Brand<string, 'ConnectionId'>;
export type ConnectorType = Brand<string, 'ConnectorType'>;

export const asTenantId = (v: string): TenantId => v as TenantId;
export const asConnectionId = (v: string): ConnectionId => v as ConnectionId;
export const asConnectorType = (v: string): ConnectorType => v as ConnectorType;

/**
 * The authenticated, RLS-scoped context for a unit of connector work.
 * `actor` records who/what triggered the operation for audit purposes.
 */
export interface TenantContext {
  readonly tenantId: TenantId;
  readonly connectionId: ConnectionId;
  /** Identifier of the principal that initiated work (user id or "system"). */
  readonly actor: string;
  /** Correlation id propagated across the event + audit rails. */
  readonly correlationId: string;
  /** Trace id for distributed tracing (OpenTelemetry compatible). */
  readonly traceId?: string;
}

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

/**
 * Structured logger. Implementations MUST redact secret material. The SDK
 * passes only non-secret structured fields.
 */
export interface Logger {
  log(level: LogLevel, message: string, fields?: Record<string, unknown>): void;
  child(bindings: Record<string, unknown>): Logger;
}

/** Injectable clock for deterministic tests. */
export interface Clock {
  now(): Date;
}

export const systemClock: Clock = { now: () => new Date() };

/** Cursor used by incremental sync. Opaque to the orchestrator. */
export type SyncCursor = Brand<string, 'SyncCursor'>;
export const asSyncCursor = (v: string): SyncCursor => v as SyncCursor;
