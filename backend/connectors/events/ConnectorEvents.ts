import { ConnectorError } from '../sdk/errors/ConnectorError';

/**
 * Connector event contracts.
 *
 * These are the canonical, versioned event payloads published to the Wave-0
 * event rail. Every event carries tenant attribution, a correlation id, and a
 * monotonic schema version so consumers can evolve safely.
 *
 * Events are facts about the past — named in the past tense — and MUST NOT
 * contain secret material or full record payloads (only counts + cursors).
 */

export const CONNECTOR_EVENT_SCHEMA_VERSION = 1 as const;

export type ConnectorEventType =
  | 'ConnectorConnected'
  | 'ConnectorDisconnected'
  | 'SyncStarted'
  | 'SyncCompleted'
  | 'SyncFailed';

export interface ConnectorEventBase {
  readonly schemaVersion: typeof CONNECTOR_EVENT_SCHEMA_VERSION;
  readonly type: ConnectorEventType;
  /** Globally unique event id (ULID/UUID), assigned by the publisher. */
  readonly eventId: string;
  readonly tenantId: string;
  readonly connectionId: string;
  readonly connectorType: string;
  readonly correlationId: string;
  /** ISO-8601 occurrence time. */
  readonly occurredAt: string;
  /** Principal that caused the event (user id or "system"). */
  readonly actor: string;
}

export interface ConnectorConnectedEvent extends ConnectorEventBase {
  readonly type: 'ConnectorConnected';
  /** Scopes granted at connection time. */
  readonly scopes: readonly string[];
}

export interface ConnectorDisconnectedEvent extends ConnectorEventBase {
  readonly type: 'ConnectorDisconnected';
  readonly reason: string;
}

export interface SyncStartedEvent extends ConnectorEventBase {
  readonly type: 'SyncStarted';
  readonly stream: string;
  readonly mode: 'full' | 'incremental';
  /** Cursor the sync resumes from, if any. */
  readonly fromCursor?: string;
  /** Unique id for this sync run, correlating Started/Completed/Failed. */
  readonly runId: string;
}

export interface SyncCompletedEvent extends ConnectorEventBase {
  readonly type: 'SyncCompleted';
  readonly stream: string;
  readonly runId: string;
  readonly recordsProcessed: number;
  readonly batches: number;
  /** Cursor persisted at completion. */
  readonly toCursor?: string;
  readonly durationMs: number;
}

export interface SyncFailedEvent extends ConnectorEventBase {
  readonly type: 'SyncFailed';
  readonly stream: string;
  readonly runId: string;
  readonly recordsProcessed: number;
  /** Error classification for routing/alerting. */
  readonly errorCategory: string;
  readonly retryable: boolean;
  readonly message: string;
  /** Cursor reached before failure; the next run resumes here. */
  readonly atCursor?: string;
  readonly durationMs: number;
}

export type ConnectorEvent =
  | ConnectorConnectedEvent
  | ConnectorDisconnectedEvent
  | SyncStartedEvent
  | SyncCompletedEvent
  | SyncFailedEvent;

/**
 * Publisher port. Production implementation forwards to the Wave-0 event rail;
 * tests use an in-memory collector.
 */
export interface ConnectorEventPublisher {
  publish(event: ConnectorEvent): Promise<void>;
}

export class InMemoryEventPublisher implements ConnectorEventPublisher {
  public readonly events: ConnectorEvent[] = [];
  async publish(event: ConnectorEvent): Promise<void> {
    this.events.push(event);
  }
}

/* ------------------------------------------------------------------ *
 * Builders — centralize event construction so required fields and the
 * schema version can never be forgotten at call sites.
 * ------------------------------------------------------------------ */

interface IdGen {
  eventId(): string;
}

export interface EventEnvelopeInput {
  readonly tenantId: string;
  readonly connectionId: string;
  readonly connectorType: string;
  readonly correlationId: string;
  readonly actor: string;
  readonly occurredAt: string;
}

export class ConnectorEventFactory {
  constructor(private readonly ids: IdGen) {}

  private base<T extends ConnectorEventType>(
    type: T,
    env: EventEnvelopeInput,
  ): ConnectorEventBase & { type: T } {
    return {
      schemaVersion: CONNECTOR_EVENT_SCHEMA_VERSION,
      type,
      eventId: this.ids.eventId(),
      tenantId: env.tenantId,
      connectionId: env.connectionId,
      connectorType: env.connectorType,
      correlationId: env.correlationId,
      occurredAt: env.occurredAt,
      actor: env.actor,
    };
  }

  connected(env: EventEnvelopeInput, scopes: readonly string[]): ConnectorConnectedEvent {
    return { ...this.base('ConnectorConnected', env), scopes };
  }

  disconnected(env: EventEnvelopeInput, reason: string): ConnectorDisconnectedEvent {
    return { ...this.base('ConnectorDisconnected', env), reason };
  }

  syncStarted(
    env: EventEnvelopeInput,
    args: { stream: string; mode: 'full' | 'incremental'; runId: string; fromCursor?: string },
  ): SyncStartedEvent {
    return { ...this.base('SyncStarted', env), ...args };
  }

  syncCompleted(
    env: EventEnvelopeInput,
    args: {
      stream: string;
      runId: string;
      recordsProcessed: number;
      batches: number;
      toCursor?: string;
      durationMs: number;
    },
  ): SyncCompletedEvent {
    return { ...this.base('SyncCompleted', env), ...args };
  }

  syncFailed(
    env: EventEnvelopeInput,
    args: {
      stream: string;
      runId: string;
      recordsProcessed: number;
      error: ConnectorError;
      atCursor?: string;
      durationMs: number;
    },
  ): SyncFailedEvent {
    return {
      ...this.base('SyncFailed', env),
      stream: args.stream,
      runId: args.runId,
      recordsProcessed: args.recordsProcessed,
      errorCategory: args.error.category,
      retryable: args.error.retryable,
      message: args.error.message,
      atCursor: args.atCursor,
      durationMs: args.durationMs,
    };
  }
}
