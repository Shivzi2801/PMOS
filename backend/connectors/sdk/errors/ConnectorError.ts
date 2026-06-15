/**
 * Connector error taxonomy.
 *
 * Every error surfaced by a connector or the SDK MUST be one of these types
 * (or a subclass). This guarantees that callers — sync orchestrators, retry
 * engines, the event rail — can make deterministic decisions about whether an
 * operation is retryable, whether credentials must be refreshed, or whether a
 * tenant must be alerted.
 *
 * Errors are intentionally *data*, not just messages. The `retryable`,
 * `category`, and `httpStatus` fields drive automated behavior.
 */

export enum ConnectorErrorCategory {
  /** Caller supplied bad input we will never accept (no retry). */
  Validation = 'VALIDATION',
  /** Credentials are missing, expired, or revoked. Refresh then retry. */
  Authentication = 'AUTHENTICATION',
  /** Authenticated but not permitted. Tenant action required (no retry). */
  Authorization = 'AUTHORIZATION',
  /** Remote system asked us to slow down. Retry after backoff. */
  RateLimited = 'RATE_LIMITED',
  /** Remote system transiently unavailable (5xx, network). Retry. */
  Transient = 'TRANSIENT',
  /** Remote returned a stable error for this request (4xx). No retry. */
  Permanent = 'PERMANENT',
  /** Our own configuration/state is wrong. No retry until fixed. */
  Configuration = 'CONFIGURATION',
  /** Data returned by remote violated our contract. No retry. */
  DataIntegrity = 'DATA_INTEGRITY',
  /** Unknown/unclassified. Treated as transient with caution. */
  Unknown = 'UNKNOWN',
}

export interface ConnectorErrorContext {
  /** Tenant the operation was running for. Never logged in plaintext secrets. */
  readonly tenantId?: string;
  /** Connector instance (connection) id. */
  readonly connectionId?: string;
  /** Logical operation, e.g. "zendesk.sync.tickets". */
  readonly operation?: string;
  /** Provider-native error code, if any. */
  readonly providerCode?: string;
  /** Arbitrary structured detail. MUST NOT contain secret material. */
  readonly detail?: Record<string, unknown>;
}

export interface ConnectorErrorOptions {
  readonly category: ConnectorErrorCategory;
  readonly retryable: boolean;
  readonly httpStatus?: number;
  /** If set, the minimum time to wait before retrying, in milliseconds. */
  readonly retryAfterMs?: number;
  readonly context?: ConnectorErrorContext;
  readonly cause?: unknown;
}

export class ConnectorError extends Error {
  public readonly category: ConnectorErrorCategory;
  public readonly retryable: boolean;
  public readonly httpStatus?: number;
  public readonly retryAfterMs?: number;
  public readonly context: ConnectorErrorContext;
  public override readonly cause?: unknown;
  public readonly occurredAt: Date;

  constructor(message: string, options: ConnectorErrorOptions) {
    super(message);
    this.name = this.constructor.name;
    this.category = options.category;
    this.retryable = options.retryable;
    this.httpStatus = options.httpStatus;
    this.retryAfterMs = options.retryAfterMs;
    this.context = options.context ?? {};
    this.cause = options.cause;
    this.occurredAt = new Date();
    // Maintains a proper stack trace where available (V8).
    if (typeof (Error as { captureStackTrace?: unknown }).captureStackTrace === 'function') {
      (Error as unknown as { captureStackTrace: (t: object, c: unknown) => void }).captureStackTrace(
        this,
        this.constructor,
      );
    }
  }

  /**
   * Serializable form safe for the event/audit rail. Deliberately excludes the
   * `cause` chain stack to avoid leaking internal detail across the wire, but
   * keeps enough to be actionable.
   */
  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      message: this.message,
      category: this.category,
      retryable: this.retryable,
      httpStatus: this.httpStatus,
      retryAfterMs: this.retryAfterMs,
      providerCode: this.context.providerCode,
      operation: this.context.operation,
      occurredAt: this.occurredAt.toISOString(),
    };
  }
}

export class ValidationError extends ConnectorError {
  constructor(message: string, context?: ConnectorErrorContext, cause?: unknown) {
    super(message, {
      category: ConnectorErrorCategory.Validation,
      retryable: false,
      httpStatus: 400,
      context,
      cause,
    });
  }
}

export class AuthenticationError extends ConnectorError {
  constructor(message: string, context?: ConnectorErrorContext, cause?: unknown) {
    super(message, {
      category: ConnectorErrorCategory.Authentication,
      // Retryable only after a credential refresh; the auth layer decides.
      retryable: true,
      httpStatus: 401,
      context,
      cause,
    });
  }
}

export class AuthorizationError extends ConnectorError {
  constructor(message: string, context?: ConnectorErrorContext, cause?: unknown) {
    super(message, {
      category: ConnectorErrorCategory.Authorization,
      retryable: false,
      httpStatus: 403,
      context,
      cause,
    });
  }
}

export class RateLimitError extends ConnectorError {
  constructor(message: string, retryAfterMs: number, context?: ConnectorErrorContext, cause?: unknown) {
    super(message, {
      category: ConnectorErrorCategory.RateLimited,
      retryable: true,
      httpStatus: 429,
      retryAfterMs,
      context,
      cause,
    });
  }
}

export class TransientError extends ConnectorError {
  constructor(message: string, context?: ConnectorErrorContext, cause?: unknown, httpStatus?: number) {
    super(message, {
      category: ConnectorErrorCategory.Transient,
      retryable: true,
      httpStatus,
      context,
      cause,
    });
  }
}

export class PermanentError extends ConnectorError {
  constructor(message: string, context?: ConnectorErrorContext, cause?: unknown, httpStatus?: number) {
    super(message, {
      category: ConnectorErrorCategory.Permanent,
      retryable: false,
      httpStatus,
      context,
      cause,
    });
  }
}

export class ConfigurationError extends ConnectorError {
  constructor(message: string, context?: ConnectorErrorContext, cause?: unknown) {
    super(message, {
      category: ConnectorErrorCategory.Configuration,
      retryable: false,
      context,
      cause,
    });
  }
}

export class DataIntegrityError extends ConnectorError {
  constructor(message: string, context?: ConnectorErrorContext, cause?: unknown) {
    super(message, {
      category: ConnectorErrorCategory.DataIntegrity,
      retryable: false,
      context,
      cause,
    });
  }
}

/**
 * Normalizes an arbitrary thrown value into a ConnectorError. Used at SDK
 * boundaries so downstream code never has to handle a raw unknown.
 */
export function toConnectorError(value: unknown, context?: ConnectorErrorContext): ConnectorError {
  if (value instanceof ConnectorError) {
    return value;
  }
  if (value instanceof Error) {
    return new ConnectorError(value.message, {
      category: ConnectorErrorCategory.Unknown,
      retryable: true,
      context,
      cause: value,
    });
  }
  return new ConnectorError('Unknown non-error thrown', {
    category: ConnectorErrorCategory.Unknown,
    retryable: true,
    context,
    cause: value,
  });
}

/** Type guard usable in catch blocks. */
export function isConnectorError(value: unknown): value is ConnectorError {
  return value instanceof ConnectorError;
}
