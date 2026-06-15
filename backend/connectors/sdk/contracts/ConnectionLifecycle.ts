import { Clock, TenantContext, systemClock } from '../interfaces/types';
import {
  ConnectionState,
  LifecycleTransition,
  canTransition,
  nextState,
} from '../interfaces/ConnectorLifecycle';
import { ConfigurationError } from '../errors/ConnectorError';
import { Logger } from '../interfaces/types';

/**
 * ConnectionLifecycle manager.
 *
 * Owns the connection state machine. It is storage-agnostic via the
 * `ConnectionStore` port and event-rail-agnostic via the `LifecycleEmitter`
 * port, so it can be unit-tested with in-memory fakes and wired to Prisma +
 * the real event rail in production.
 *
 * Concurrency: transitions for a single connection MUST be serialized by the
 * caller (the store's `withLock` provides row-level locking in Postgres).
 */

export interface ConnectionRecord {
  readonly tenantId: string;
  readonly connectionId: string;
  readonly connectorType: string;
  readonly state: ConnectionState;
  readonly stateReason?: string;
  readonly updatedAt: Date;
}

export interface ConnectionStore {
  load(tenantId: string, connectionId: string): Promise<ConnectionRecord | null>;
  /** Persist a state change. Implementations enforce RLS on tenantId. */
  updateState(
    tenantId: string,
    connectionId: string,
    state: ConnectionState,
    reason: string | undefined,
    at: Date,
  ): Promise<ConnectionRecord>;
  /** Run `fn` while holding a row lock on the connection. */
  withLock<T>(tenantId: string, connectionId: string, fn: () => Promise<T>): Promise<T>;
}

export interface LifecycleEmitter {
  connectorConnected(context: TenantContext, connectorType: string): Promise<void>;
  connectorDisconnected(
    context: TenantContext,
    connectorType: string,
    reason: string,
  ): Promise<void>;
}

export class ConnectionLifecycle {
  constructor(
    private readonly store: ConnectionStore,
    private readonly emitter: LifecycleEmitter,
    private readonly logger: Logger,
    private readonly clock: Clock = systemClock,
  ) {}

  /**
   * Apply a transition. Validates legality against the current persisted state,
   * persists the new state, and emits lifecycle events where applicable.
   */
  async transition(
    context: TenantContext,
    transition: LifecycleTransition,
  ): Promise<ConnectionRecord> {
    return this.store.withLock(context.tenantId, context.connectionId, async () => {
      const current = await this.store.load(context.tenantId, context.connectionId);
      if (!current) {
        throw new ConfigurationError('Connection not found', {
          tenantId: context.tenantId,
          connectionId: context.connectionId,
        });
      }

      if (!canTransition(current.state, transition.type)) {
        throw new ConfigurationError(
          `Illegal transition ${transition.type} from ${current.state}`,
          {
            tenantId: context.tenantId,
            connectionId: context.connectionId,
            detail: { from: current.state, transition: transition.type },
          },
        );
      }

      const target = nextState(current.state, transition.type);
      const reason = 'reason' in transition ? transition.reason : undefined;
      const updated = await this.store.updateState(
        context.tenantId,
        context.connectionId,
        target,
        reason,
        this.clock.now(),
      );

      this.logger.log('info', 'connection.transition', {
        from: current.state,
        to: target,
        transition: transition.type,
        connectionId: context.connectionId,
      });

      // Emit lifecycle events on entering meaningful terminal/active states.
      const becameActive =
        target === ConnectionState.Active && current.state !== ConnectionState.Active;
      if (becameActive) {
        await this.emitter.connectorConnected(context, updated.connectorType);
      }
      if (target === ConnectionState.Disconnected) {
        await this.emitter.connectorDisconnected(
          context,
          updated.connectorType,
          reason ?? 'unspecified',
        );
      }

      return updated;
    });
  }
}
