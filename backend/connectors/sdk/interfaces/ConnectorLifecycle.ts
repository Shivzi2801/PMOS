import { TenantContext } from './types';

/**
 * Connector lifecycle contracts.
 *
 * A connection moves through a strict state machine. The lifecycle manager is
 * the only component permitted to transition state, and it emits the
 * corresponding events (ConnectorConnected/Disconnected) on the event rail.
 *
 *   PENDING ──connect──▶ ACTIVE ──disconnect──▶ DISCONNECTED
 *      │                   │  ▲                       ▲
 *      │                   │  └──reactivate───────────┘
 *      └──fail──▶ ERROR ───┘ (after fix)
 *
 * ACTIVE ──degrade──▶ DEGRADED ──recover──▶ ACTIVE
 * Any state ──disconnect──▶ DISCONNECTED (terminal until reconnect)
 */

export enum ConnectionState {
  /** Created but not yet authorized / validated. */
  Pending = 'PENDING',
  /** Authorized, health-checked, syncing normally. */
  Active = 'ACTIVE',
  /** Authorized but failing health checks / partial degradation. */
  Degraded = 'DEGRADED',
  /** Hard failure requiring tenant action (e.g. revoked credential). */
  Error = 'ERROR',
  /** Intentionally disconnected. Secrets revoked + deleted. */
  Disconnected = 'DISCONNECTED',
}

export type LifecycleTransition =
  | { type: 'CONNECT' }
  | { type: 'DEGRADE'; reason: string }
  | { type: 'RECOVER' }
  | { type: 'FAIL'; reason: string }
  | { type: 'DISCONNECT'; reason: string }
  | { type: 'REACTIVATE' };

/**
 * Allowed transitions. Any pair not present here is illegal and the lifecycle
 * manager throws a ConfigurationError if attempted.
 */
export const ALLOWED_TRANSITIONS: Readonly<
  Record<ConnectionState, ReadonlyArray<LifecycleTransition['type']>>
> = {
  [ConnectionState.Pending]: ['CONNECT', 'FAIL', 'DISCONNECT'],
  [ConnectionState.Active]: ['DEGRADE', 'FAIL', 'DISCONNECT'],
  [ConnectionState.Degraded]: ['RECOVER', 'FAIL', 'DISCONNECT'],
  [ConnectionState.Error]: ['REACTIVATE', 'DISCONNECT'],
  [ConnectionState.Disconnected]: ['REACTIVATE'],
};

export function canTransition(
  from: ConnectionState,
  transition: LifecycleTransition['type'],
): boolean {
  return ALLOWED_TRANSITIONS[from].includes(transition);
}

/** Resolve the destination state for a legal transition. */
export function nextState(
  from: ConnectionState,
  transition: LifecycleTransition['type'],
): ConnectionState {
  switch (transition) {
    case 'CONNECT':
    case 'RECOVER':
    case 'REACTIVATE':
      return ConnectionState.Active;
    case 'DEGRADE':
      return ConnectionState.Degraded;
    case 'FAIL':
      return ConnectionState.Error;
    case 'DISCONNECT':
      return ConnectionState.Disconnected;
    default: {
      const _exhaustive: never = transition;
      return _exhaustive;
    }
  }
}

export interface LifecycleHooks {
  /** Called once a connection becomes ACTIVE for the first time. */
  onConnected?(context: TenantContext): Promise<void>;
  /** Called when a connection moves to DISCONNECTED. */
  onDisconnected?(context: TenantContext, reason: string): Promise<void>;
}

/**
 * The lifecycle-aware portion of a connector. Most connectors only need the
 * hooks; the state machine itself lives in the SDK's ConnectionLifecycle
 * manager (see contracts/ConnectionLifecycle.ts).
 */
export interface ConnectorLifecycle {
  readonly hooks?: LifecycleHooks;
}
