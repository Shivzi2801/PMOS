import { Clock } from '../sdk/interfaces/types';
import { HttpClient, HttpRequest, HttpResponse } from '../zendesk/http';
import { SecretRef, SecretStore, StoredSecret } from '../secrets/model/SecretModel';
import { SecretEnvelope } from '../secrets/service/SecretCrypto';
import {
  ConnectionRecord,
  ConnectionStore,
} from '../sdk/contracts/ConnectionLifecycle';
import { ConnectionState } from '../sdk/interfaces/ConnectorLifecycle';
import {
  RecordSink,
  SyncRunStore,
  SyncStateStore,
  IdGenerator,
} from '../sdk/contracts/SyncOrchestrator';
import { SyncCursor, TenantContext } from '../sdk/interfaces/types';
import { SyncRecord, SyncMode } from '../sdk/interfaces/ConnectorSync';

/** Deterministic, advanceable clock. */
export class FakeClock implements Clock {
  constructor(private current = new Date('2026-06-15T00:00:00.000Z')) {}
  now(): Date {
    return new Date(this.current);
  }
  advance(ms: number): void {
    this.current = new Date(this.current.getTime() + ms);
  }
}

/** Deterministic id generator. */
export class SeqIds implements IdGenerator {
  private r = 0;
  private e = 0;
  runId(): string {
    return `run-${++this.r}`;
  }
  eventId(): string {
    return `evt-${++this.e}`;
  }
}

/** Programmable HTTP stub: queue responses or supply a handler. */
export class StubHttp implements HttpClient {
  public readonly requests: HttpRequest[] = [];
  private queue: HttpResponse[] = [];
  private handler?: (req: HttpRequest, n: number) => HttpResponse;

  enqueue(...res: HttpResponse[]): this {
    this.queue.push(...res);
    return this;
  }
  onRequest(fn: (req: HttpRequest, n: number) => HttpResponse): this {
    this.handler = fn;
    return this;
  }
  async request(req: HttpRequest): Promise<HttpResponse> {
    this.requests.push(req);
    if (this.handler) return this.handler(req, this.requests.length);
    const next = this.queue.shift();
    if (!next) throw new Error('StubHttp: no response queued');
    return next;
  }
}

export function json(status: number, body: unknown, headers: Record<string, string> = {}): HttpResponse {
  return { status, headers, body: JSON.stringify(body) };
}

/** In-memory SecretStore with versioning + single-ACTIVE invariant. */
export class MemSecretStore implements SecretStore {
  private rows: StoredSecret[] = [];
  private seq = 0;

  private key(ref: SecretRef): string {
    return `${ref.tenantId}|${ref.connectionId}|${ref.name}`;
  }

  async getActive(ref: SecretRef): Promise<StoredSecret | null> {
    return (
      this.rows.find(
        (r) =>
          r.tenantId === ref.tenantId &&
          r.connectionId === ref.connectionId &&
          r.name === ref.name &&
          r.status === 'ACTIVE',
      ) ?? null
    );
  }
  async getVersion(ref: SecretRef, version: number): Promise<StoredSecret | null> {
    return (
      this.rows.find(
        (r) =>
          r.tenantId === ref.tenantId &&
          r.connectionId === ref.connectionId &&
          r.name === ref.name &&
          r.version === version,
      ) ?? null
    );
  }
  async listVersions(ref: SecretRef): Promise<readonly StoredSecret[]> {
    return this.rows
      .filter((r) => r.tenantId === ref.tenantId && r.connectionId === ref.connectionId && r.name === ref.name)
      .sort((a, b) => b.version - a.version);
  }
  async putNewVersion(input: {
    ref: SecretRef;
    envelope: SecretEnvelope;
    expiresAt?: number;
  }): Promise<StoredSecret> {
    const { ref, envelope, expiresAt } = input;
    for (const r of this.rows) {
      if (r.tenantId === ref.tenantId && r.connectionId === ref.connectionId && r.name === ref.name && r.status === 'ACTIVE') {
        (r as { status: string }).status = 'SUPERSEDED';
        (r as { rotatedAt?: Date }).rotatedAt = new Date();
      }
    }
    const maxVersion = Math.max(
      0,
      ...this.rows
        .filter((r) => r.tenantId === ref.tenantId && r.connectionId === ref.connectionId && r.name === ref.name)
        .map((r) => r.version),
    );
    const stored: StoredSecret = {
      id: `sec-${++this.seq}`,
      tenantId: ref.tenantId,
      connectionId: ref.connectionId,
      name: ref.name,
      version: maxVersion + 1,
      status: 'ACTIVE',
      envelope,
      createdAt: new Date(),
      expiresAt,
    };
    this.rows.push(stored);
    return stored;
  }
  async revokeAll(ref: SecretRef): Promise<number> {
    let n = 0;
    for (const r of this.rows) {
      if (r.tenantId === ref.tenantId && r.connectionId === ref.connectionId && r.name === ref.name && r.status !== 'REVOKED') {
        (r as { status: string }).status = 'REVOKED';
        n++;
      }
    }
    return n;
  }
  async deleteAll(ref: Omit<SecretRef, 'name'>): Promise<number> {
    const before = this.rows.length;
    this.rows = this.rows.filter((r) => !(r.tenantId === ref.tenantId && r.connectionId === ref.connectionId));
    return before - this.rows.length;
  }
  async withLock<T>(_ref: SecretRef, fn: () => Promise<T>): Promise<T> {
    return fn();
  }
}

/** In-memory connection store for lifecycle tests. */
export class MemConnectionStore implements ConnectionStore {
  private rows = new Map<string, ConnectionRecord>();
  seed(rec: ConnectionRecord): void {
    this.rows.set(`${rec.tenantId}|${rec.connectionId}`, rec);
  }
  async load(tenantId: string, connectionId: string): Promise<ConnectionRecord | null> {
    return this.rows.get(`${tenantId}|${connectionId}`) ?? null;
  }
  async updateState(
    tenantId: string,
    connectionId: string,
    state: ConnectionState,
    reason: string | undefined,
    at: Date,
  ): Promise<ConnectionRecord> {
    const key = `${tenantId}|${connectionId}`;
    const cur = this.rows.get(key);
    if (!cur) throw new Error('not found');
    const next: ConnectionRecord = { ...cur, state, stateReason: reason, updatedAt: at };
    this.rows.set(key, next);
    return next;
  }
  async withLock<T>(_t: string, _c: string, fn: () => Promise<T>): Promise<T> {
    return fn();
  }
}

/** In-memory sync state + run stores + sink for orchestrator tests. */
export class MemSyncState implements SyncStateStore {
  private cursors = new Map<string, SyncCursor>();
  private key(c: TenantContext, stream: string): string {
    return `${c.tenantId}|${c.connectionId}|${stream}`;
  }
  async getCursor(context: TenantContext, stream: string): Promise<SyncCursor | undefined> {
    return this.cursors.get(this.key(context, stream));
  }
  async setCursor(context: TenantContext, stream: string, cursor: SyncCursor | undefined): Promise<void> {
    if (cursor === undefined) this.cursors.delete(this.key(context, stream));
    else this.cursors.set(this.key(context, stream), cursor);
  }
}

export class MemSyncRuns implements SyncRunStore {
  public opened: unknown[] = [];
  public completed: unknown[] = [];
  public failed: unknown[] = [];
  async open(input: { context: TenantContext; stream: string; runId: string; mode: SyncMode; fromCursor?: SyncCursor }): Promise<void> {
    this.opened.push(input);
  }
  async complete(input: unknown): Promise<void> {
    this.completed.push(input);
  }
  async fail(input: unknown): Promise<void> {
    this.failed.push(input);
  }
}

export class MemSink implements RecordSink {
  public readonly written: SyncRecord[] = [];
  private failOnBatch?: number;
  private batchCount = 0;
  failAt(batch: number): this {
    this.failOnBatch = batch;
    return this;
  }
  async write(_context: TenantContext, records: readonly SyncRecord[]): Promise<void> {
    this.batchCount++;
    if (this.failOnBatch === this.batchCount) {
      throw new Error(`sink failure on batch ${this.batchCount}`);
    }
    this.written.push(...records);
  }
}
