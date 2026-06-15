import { describe, it, expect } from 'vitest';
import {
  ConnectionState,
  canTransition,
  nextState,
} from '../../sdk/interfaces/ConnectorLifecycle';
import { ConnectionLifecycle, LifecycleEmitter } from '../../sdk/contracts/ConnectionLifecycle';
import { MemConnectionStore, FakeClock } from '../fakes';
import { TenantContext, asConnectionId, asTenantId } from '../../sdk/interfaces/types';

const noopLogger = {
  log: () => {},
  child: () => noopLogger,
};

class RecordingEmitter implements LifecycleEmitter {
  connected: string[] = [];
  disconnected: Array<{ reason: string }> = [];
  async connectorConnected(): Promise<void> {
    this.connected.push('connected');
  }
  async connectorDisconnected(_c: TenantContext, _t: string, reason: string): Promise<void> {
    this.disconnected.push({ reason });
  }
}

const ctx: TenantContext = {
  tenantId: asTenantId('t1'),
  connectionId: asConnectionId('c1'),
  actor: 'user-1',
  correlationId: 'corr',
};

describe('lifecycle state machine', () => {
  it('permits only legal transitions', () => {
    expect(canTransition(ConnectionState.Pending, 'CONNECT')).toBe(true);
    expect(canTransition(ConnectionState.Pending, 'RECOVER')).toBe(false);
    expect(canTransition(ConnectionState.Active, 'DEGRADE')).toBe(true);
    expect(canTransition(ConnectionState.Disconnected, 'CONNECT')).toBe(false);
    expect(canTransition(ConnectionState.Disconnected, 'REACTIVATE')).toBe(true);
  });

  it('resolves destination states', () => {
    expect(nextState(ConnectionState.Pending, 'CONNECT')).toBe(ConnectionState.Active);
    expect(nextState(ConnectionState.Active, 'DEGRADE')).toBe(ConnectionState.Degraded);
    expect(nextState(ConnectionState.Active, 'FAIL')).toBe(ConnectionState.Error);
    expect(nextState(ConnectionState.Active, 'DISCONNECT')).toBe(ConnectionState.Disconnected);
  });
});

describe('ConnectionLifecycle manager', () => {
  function setup(initial: ConnectionState) {
    const store = new MemConnectionStore();
    store.seed({
      tenantId: 't1',
      connectionId: 'c1',
      connectorType: 'zendesk',
      state: initial,
      updatedAt: new Date(),
    });
    const emitter = new RecordingEmitter();
    const mgr = new ConnectionLifecycle(store, emitter, noopLogger, new FakeClock());
    return { store, emitter, mgr };
  }

  it('emits ConnectorConnected on entering ACTIVE', async () => {
    const { mgr, emitter } = setup(ConnectionState.Pending);
    const rec = await mgr.transition(ctx, { type: 'CONNECT' });
    expect(rec.state).toBe(ConnectionState.Active);
    expect(emitter.connected).toHaveLength(1);
  });

  it('emits ConnectorDisconnected with reason on DISCONNECT', async () => {
    const { mgr, emitter } = setup(ConnectionState.Active);
    await mgr.transition(ctx, { type: 'DISCONNECT', reason: 'user revoked' });
    expect(emitter.disconnected).toEqual([{ reason: 'user revoked' }]);
  });

  it('rejects illegal transitions with a configuration error', async () => {
    const { mgr } = setup(ConnectionState.Disconnected);
    await expect(mgr.transition(ctx, { type: 'CONNECT' })).rejects.toMatchObject({
      category: 'CONFIGURATION',
    });
  });

  it('does not double-emit connected when already active', async () => {
    const { mgr, emitter } = setup(ConnectionState.Degraded);
    await mgr.transition(ctx, { type: 'RECOVER' });
    expect(emitter.connected).toHaveLength(1);
  });
});
