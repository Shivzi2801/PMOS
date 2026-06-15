import { SyncCursor, TenantContext } from './types';

/**
 * Sync interface.
 *
 * A connector exposes one or more named "streams" (e.g. tickets, users,
 * organizations for Zendesk). Each stream supports incremental sync driven by
 * an opaque cursor. The orchestrator owns cursor persistence; the connector
 * owns cursor interpretation.
 *
 * Records are yielded as an async iterable so connectors can stream pages
 * without buffering an entire dataset in memory — essential for large tenants.
 */

export type SyncMode = 'full' | 'incremental';

export interface StreamDescriptor {
  /** Stable stream name, unique within the connector. */
  readonly name: string;
  /** Whether this stream supports incremental cursors. */
  readonly supportsIncremental: boolean;
  /** Primary key field path within each record, for upsert/dedup. */
  readonly primaryKey: string;
  /** Cursor field path used to order/resume, if incremental. */
  readonly cursorField?: string;
}

/** A single normalized record emitted by a stream. */
export interface SyncRecord<T = Record<string, unknown>> {
  readonly stream: string;
  /** Value of the primary key for this record. */
  readonly id: string;
  /** Normalized payload. Connector-specific shape. */
  readonly data: T;
  /**
   * Cursor value associated with this record (e.g. updated_at epoch). Used by
   * the orchestrator to advance the persisted cursor only after a record (and
   * everything before it) has been durably written.
   */
  readonly cursor?: SyncCursor;
  /** True if the record was deleted at the source (tombstone). */
  readonly deleted?: boolean;
}

export interface SyncRequest {
  readonly context: TenantContext;
  readonly stream: string;
  readonly mode: SyncMode;
  /** Resume point for incremental sync. Undefined => start from beginning. */
  readonly cursor?: SyncCursor;
  /** Soft page size hint. The connector may clamp to provider limits. */
  readonly pageSizeHint?: number;
  /** Cancellation. */
  readonly signal?: AbortSignal;
}

export interface SyncBatch<T = Record<string, unknown>> {
  readonly records: readonly SyncRecord<T>[];
  /** Cursor to persist AFTER this batch is durably written. */
  readonly nextCursor?: SyncCursor;
  /** True if more batches remain. */
  readonly hasMore: boolean;
}

/**
 * The sync contract a connector must implement.
 */
export interface ConnectorSync {
  /** Enumerate the streams this connector exposes. */
  listStreams(context: TenantContext): Promise<readonly StreamDescriptor[]>;

  /**
   * Perform an incremental (or full) sync of a single stream, yielding
   * batches. The orchestrator persists records and advances the cursor between
   * batches, giving at-least-once delivery with resumability.
   *
   * Implementations MUST:
   *  - Respect `request.signal` and stop promptly when aborted.
   *  - Order records so that advancing `nextCursor` never skips unwritten data.
   *  - Surface provider errors as ConnectorError subclasses.
   */
  sync<T = Record<string, unknown>>(request: SyncRequest): AsyncIterable<SyncBatch<T>>;
}
