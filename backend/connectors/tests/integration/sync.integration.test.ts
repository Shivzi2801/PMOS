import { describe, it, expect } from 'vitest';
import { ZendeskConnector } from '../../zendesk/ZendeskConnector';
import { SyncOrchestrator } from '../../sdk/contracts/SyncOrchestrator';
import { InMemoryEventPublisher } from '../../events/ConnectorEvents';
import {
  StubHttp,
  json,
  MemSyncState,
  MemSyncRuns,
  MemSink,
  SeqIds,
  FakeClock,
} from '../fakes';
import { TenantContext, asConnectionId, asTenantId } from '../../sdk/interfaces/types';
import { Connector } from '../../sdk/interfaces/Connector';

const ctx: TenantContext = {
  tenantId: asTenantId('t1'),
  connectionId: asConnectionId('c1'),
  actor: 'system',
  correlationId: 'corr',
};

function buildConnector(http: StubHttp): Connector {
  return new ZendeskConnector({
    clientId: 'cid',
    clientSecret: 'csec',
    config: { subdomain: 'acme' },
    http,
    credentialProvider: async () => ({
      credential: { accessToken: 'AT', tokenType: 'bearer', scope: 'read' },
      authHeaders: { authorization: 'Bearer AT' },
    }),
    now: () => 1_000_000,
  }) as unknown as Connector;
}

function buildOrchestrator(connector: Connector, sink: MemSink) {
  const stateStore = new MemSyncState();
  const runStore = new MemSyncRuns();
  const publisher = new InMemoryEventPublisher();
  const orch = new SyncOrchestrator({
    connector,
    sink,
    stateStore,
    runStore,
    publisher,
    ids: new SeqIds(),
    clock: new FakeClock(),
  });
  return { orch, stateStore, runStore, publisher };
}

describe('Zendesk incremental sync (integration)', () => {
  it('paginates with cursor until end_of_stream and persists records + cursor', async () => {
    const http = new StubHttp()
      .enqueue(
        json(200, {
          tickets: [{ id: 1, updated_at: 'a' }, { id: 2, updated_at: 'b' }],
          after_cursor: 'CUR1',
          end_of_stream: false,
        }),
      )
      .enqueue(
        json(200, {
          tickets: [{ id: 3, updated_at: 'c' }],
          after_cursor: 'CUR2',
          end_of_stream: true,
        }),
      );
    const connector = buildConnector(http);
    const sink = new MemSink();
    const { orch, stateStore, runStore, publisher } = buildOrchestrator(connector, sink);

    const outcome = await orch.run({ context: ctx, stream: 'tickets', mode: 'incremental' });

    expect(outcome.recordsProcessed).toBe(3);
    expect(outcome.batches).toBe(2);
    expect(sink.written.map((r) => r.id)).toEqual(['1', '2', '3']);

    // Cursor persisted, decodes to CUR2.
    const cursor = await stateStore.getCursor(ctx, 'tickets');
    expect(cursor).toBeTruthy();
    const decoded = JSON.parse(Buffer.from(String(cursor), 'base64url').toString('utf8'));
    expect(decoded.cursor).toBe('CUR2');

    // Second request used the first cursor.
    expect(http.requests[1].url).toContain('cursor=CUR1');

    // Events: started + completed.
    const types = publisher.events.map((e) => e.type);
    expect(types).toEqual(['SyncStarted', 'SyncCompleted']);
    expect(runStore.completed).toHaveLength(1);
  });

  it('on sink failure emits SyncFailed and persists cursor reached so far', async () => {
    const http = new StubHttp()
      .enqueue(
        json(200, {
          tickets: [{ id: 1, updated_at: 'a' }],
          after_cursor: 'CUR1',
          end_of_stream: false,
        }),
      )
      .enqueue(
        json(200, {
          tickets: [{ id: 2, updated_at: 'b' }],
          after_cursor: 'CUR2',
          end_of_stream: true,
        }),
      );
    const connector = buildConnector(http);
    const sink = new MemSink().failAt(2); // second batch write fails
    const { orch, stateStore, runStore, publisher } = buildOrchestrator(connector, sink);

    await expect(orch.run({ context: ctx, stream: 'tickets', mode: 'incremental' })).rejects.toBeTruthy();

    // First batch persisted + cursor advanced to CUR1, second batch not written.
    expect(sink.written.map((r) => r.id)).toEqual(['1']);
    const cursor = await stateStore.getCursor(ctx, 'tickets');
    const decoded = JSON.parse(Buffer.from(String(cursor), 'base64url').toString('utf8'));
    expect(decoded.cursor).toBe('CUR1');

    const types = publisher.events.map((e) => e.type);
    expect(types).toEqual(['SyncStarted', 'SyncFailed']);
    expect(runStore.failed).toHaveLength(1);
  });

  it('resumes from a persisted cursor on a subsequent run', async () => {
    // First run completes with CUR1.
    const http1 = new StubHttp().enqueue(
      json(200, { tickets: [{ id: 1, updated_at: 'a' }], after_cursor: 'CUR1', end_of_stream: true }),
    );
    const connector1 = buildConnector(http1);
    const sink1 = new MemSink();
    const ctxState = new MemSyncState();
    const orch1 = new SyncOrchestrator({
      connector: connector1,
      sink: sink1,
      stateStore: ctxState,
      runStore: new MemSyncRuns(),
      publisher: new InMemoryEventPublisher(),
      ids: new SeqIds(),
      clock: new FakeClock(),
    });
    await orch1.run({ context: ctx, stream: 'tickets', mode: 'incremental' });

    // Second run should send cursor=CUR1.
    const http2 = new StubHttp().enqueue(
      json(200, { tickets: [], after_cursor: 'CUR1', end_of_stream: true }),
    );
    const connector2 = buildConnector(http2);
    const orch2 = new SyncOrchestrator({
      connector: connector2,
      sink: new MemSink(),
      stateStore: ctxState, // same persisted state
      runStore: new MemSyncRuns(),
      publisher: new InMemoryEventPublisher(),
      ids: new SeqIds(),
      clock: new FakeClock(),
    });
    await orch2.run({ context: ctx, stream: 'tickets', mode: 'incremental' });
    expect(http2.requests[0].url).toContain('cursor=CUR1');
  });

  it('retries a transient 503 then succeeds within the sync', async () => {
    let calls = 0;
    const http = new StubHttp().onRequest(() => {
      calls++;
      if (calls === 1) return { status: 503, headers: {}, body: '' };
      return json(200, { tickets: [{ id: 9, updated_at: 'z' }], after_cursor: 'C', end_of_stream: true });
    });
    const connector = buildConnector(http);
    const sink = new MemSink();
    const { orch } = buildOrchestrator(connector, sink);
    const outcome = await orch.run({ context: ctx, stream: 'tickets', mode: 'incremental' });
    expect(outcome.recordsProcessed).toBe(1);
    expect(calls).toBe(2);
  });
});

describe('Zendesk health check (integration)', () => {
  it('reports healthy on 200', async () => {
    const http = new StubHttp().enqueue(json(200, { user: { id: 1 } }));
    const connector = buildConnector(http);
    const res = await connector.health.check(ctx);
    expect(res.status).toBe('healthy');
  });
  it('reports unhealthy on 401', async () => {
    const http = new StubHttp().enqueue(json(401, { error: 'unauth' }));
    const connector = buildConnector(http);
    const res = await connector.health.check(ctx);
    expect(res.status).toBe('unhealthy');
  });
});
