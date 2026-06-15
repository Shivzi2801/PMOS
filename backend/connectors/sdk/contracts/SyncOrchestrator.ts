import { Clock, SyncCursor, TenantContext, asSyncCursor, systemClock } from '../interfaces/types';
import { Connector } from '../interfaces/Connector';
import { SyncMode, SyncRecord } from '../interfaces/ConnectorSync';
import { toConnectorError } from '../errors/ConnectorError';
import {
  ConnectorEventFactory,
  ConnectorEventPublisher,
  EventEnvelopeInput,
} from '../../events/ConnectorEvents';

/**
 * SyncOrchestrator
 *
 * Drives a single stream sync for a connection end to end:
 *   1. Open a sync run + emit SyncStarted.
 *   2. Pull batches from the connector, hand records to the RecordSink, and —
 *      only after a batch is durably written — advance the persisted cursor.
 *      This yields at-least-once delivery with safe resumability: a crash mid
 *      stream resumes from the last persisted cursor, re-delivering at most one
 *      batch.
 *   3. On success emit SyncCompleted; on failure emit SyncFailed with the
 *      cursor reached, so the next run resumes from there.
 *
 * The orchestrator is storage/event agnostic via injected ports, making it
 * fully unit-testable with in-memory fakes.
 */

export interface RecordSink {
  /** Durably persist a batch of records for a tenant. Must be idempotent on id. */
  write(context: TenantContext, records: readonly SyncRecord[]): Promise<void>;
}

export interface SyncStateStore {
  getCursor(context: TenantContext, stream: string): Promise<SyncCursor | undefined>;
  setCursor(context: TenantContext, stream: string, cursor: SyncCursor | undefined): Promise<void>;
}

export interface SyncRunStore {
  open(input: {
    context: TenantContext;
    stream: string;
    runId: string;
    mode: SyncMode;
    fromCursor?: SyncCursor;
  }): Promise<void>;
  complete(input: {
    context: TenantContext;
    runId: string;
    recordsProcessed: number;
    batches: number;
    toCursor?: SyncCursor;
  }): Promise<void>;
  fail(input: {
    context: TenantContext;
    runId: string;
    recordsProcessed: number;
    errorCategory: string;
    errorMessage: string;
    atCursor?: SyncCursor;
  }): Promise<void>;
}

export interface IdGenerator {
  runId(): string;
  eventId(): string;
}

export interface SyncOrchestratorDeps {
  readonly connector: Connector;
  readonly sink: RecordSink;
  readonly stateStore: SyncStateStore;
  readonly runStore: SyncRunStore;
  readonly publisher: ConnectorEventPublisher;
  readonly ids: IdGenerator;
  readonly clock?: Clock;
}

export interface SyncRequestInput {
  readonly context: TenantContext;
  readonly stream: string;
  readonly mode: SyncMode;
  readonly pageSizeHint?: number;
  readonly signal?: AbortSignal;
}

export interface SyncOutcome {
  readonly runId: string;
  readonly recordsProcessed: number;
  readonly batches: number;
  readonly toCursor?: SyncCursor;
}

export class SyncOrchestrator {
  private readonly factory: ConnectorEventFactory;
  private readonly clock: Clock;

  constructor(private readonly deps: SyncOrchestratorDeps) {
    this.factory = new ConnectorEventFactory({ eventId: () => deps.ids.eventId() });
    this.clock = deps.clock ?? systemClock;
  }

  private env(context: TenantContext): EventEnvelopeInput {
    return {
      tenantId: context.tenantId,
      connectionId: context.connectionId,
      connectorType: this.deps.connector.metadata.type as unknown as string,
      correlationId: context.correlationId,
      actor: context.actor,
      occurredAt: this.clock.now().toISOString(),
    };
  }

  async run(input: SyncRequestInput): Promise<SyncOutcome> {
    const runId = this.deps.ids.runId();
    const startMs = this.clock.now().getTime();
    const fromCursor = await this.deps.stateStore.getCursor(input.context, input.stream);

    await this.deps.runStore.open({
      context: input.context,
      stream: input.stream,
      runId,
      mode: input.mode,
      fromCursor,
    });
    await this.deps.publisher.publish(
      this.factory.syncStarted(this.env(input.context), {
        stream: input.stream,
        mode: input.mode,
        runId,
        fromCursor: fromCursor ? String(fromCursor) : undefined,
      }),
    );

    let recordsProcessed = 0;
    let batches = 0;
    let cursor: SyncCursor | undefined = fromCursor;

    try {
      const iterable = this.deps.connector.sync.sync({
        context: input.context,
        stream: input.stream,
        mode: input.mode,
        cursor: fromCursor,
        pageSizeHint: input.pageSizeHint,
        signal: input.signal,
      });

      for await (const batch of iterable) {
        if (input.signal?.aborted) break;
        // 1) durably persist records
        await this.deps.sink.write(input.context, batch.records);
        recordsProcessed += batch.records.length;
        batches += 1;
        // 2) advance cursor only AFTER the write succeeds
        if (batch.nextCursor !== undefined) {
          cursor = batch.nextCursor;
          await this.deps.stateStore.setCursor(input.context, input.stream, cursor);
        }
        if (!batch.hasMore) break;
      }

      await this.deps.runStore.complete({
        context: input.context,
        runId,
        recordsProcessed,
        batches,
        toCursor: cursor,
      });
      await this.deps.publisher.publish(
        this.factory.syncCompleted(this.env(input.context), {
          stream: input.stream,
          runId,
          recordsProcessed,
          batches,
          toCursor: cursor ? String(cursor) : undefined,
          durationMs: this.clock.now().getTime() - startMs,
        }),
      );

      return { runId, recordsProcessed, batches, toCursor: cursor };
    } catch (raw) {
      const error = toConnectorError(raw, {
        tenantId: input.context.tenantId,
        connectionId: input.context.connectionId,
        operation: `sync.${input.stream}`,
      });
      await this.deps.runStore.fail({
        context: input.context,
        runId,
        recordsProcessed,
        errorCategory: error.category,
        errorMessage: error.message,
        atCursor: cursor,
      });
      await this.deps.publisher.publish(
        this.factory.syncFailed(this.env(input.context), {
          stream: input.stream,
          runId,
          recordsProcessed,
          error,
          atCursor: cursor ? String(cursor) : undefined,
          durationMs: this.clock.now().getTime() - startMs,
        }),
      );
      throw error;
    }
  }
}

export const __test__ = { asSyncCursor };
