import {
  ConnectorSync,
  StreamDescriptor,
  SyncBatch,
  SyncRecord,
  SyncRequest,
} from '../../sdk/interfaces/ConnectorSync';
import { SyncCursor, TenantContext, asSyncCursor } from '../../sdk/interfaces/types';
import { DataIntegrityError, toConnectorError } from '../../sdk/errors/ConnectorError';
import { executeWithRetry } from '../../sdk/retry/retry';
import { HttpClient, mapResponseToError } from '../http';
import { ZendeskConfig, ZendeskOAuthCredential, zendeskBaseUrl } from '../types';

/**
 * Zendesk incremental sync.
 *
 * Uses Zendesk's cursor-based Incremental Export API:
 *   GET /api/v2/incremental/{resource}/cursor.json?start_time=<epoch>
 *   GET /api/v2/incremental/{resource}/cursor.json?cursor=<token>
 *
 * The cursor we persist is a JSON blob { cursor?, startTime? } base64url
 * encoded, so we can resume by either the opaque Zendesk cursor (preferred) or
 * an epoch start_time (first run / fallback). The Zendesk API returns
 * `end_of_stream` to indicate the incremental window is exhausted.
 *
 * The credential is resolved by the orchestrator (via SecretService) and passed
 * in through `credentialProvider` so this class never touches the secret store
 * directly — keeping sync logic pure and testable.
 */

export type CredentialProvider = (context: TenantContext) => Promise<{
  credential: ZendeskOAuthCredential;
  authHeaders: Record<string, string>;
}>;

interface ZendeskCursorState {
  cursor?: string;
  startTime?: number;
}

interface ZendeskIncrementalResponse {
  tickets?: unknown[];
  users?: unknown[];
  organizations?: unknown[];
  after_cursor?: string;
  end_of_stream?: boolean;
}

const STREAMS: Record<string, { resource: string; key: string; cursorField: string }> = {
  tickets: { resource: 'tickets', key: 'id', cursorField: 'updated_at' },
  users: { resource: 'users', key: 'id', cursorField: 'updated_at' },
  organizations: { resource: 'organizations', key: 'id', cursorField: 'updated_at' },
};

export interface ZendeskSyncOptions {
  readonly config: ZendeskConfig;
  readonly http: HttpClient;
  readonly credentialProvider: CredentialProvider;
  readonly now?: () => number;
  readonly defaultPageSize?: number;
}

function encodeCursor(state: ZendeskCursorState): SyncCursor {
  return asSyncCursor(Buffer.from(JSON.stringify(state), 'utf8').toString('base64url'));
}

function decodeCursor(cursor?: SyncCursor): ZendeskCursorState {
  if (!cursor) return {};
  try {
    return JSON.parse(Buffer.from(String(cursor), 'base64url').toString('utf8')) as ZendeskCursorState;
  } catch {
    return {};
  }
}

export class ZendeskSync implements ConnectorSync {
  constructor(private readonly options: ZendeskSyncOptions) {}

  private now(): number {
    return this.options.now ? this.options.now() : Date.now();
  }

  async listStreams(_context: TenantContext): Promise<readonly StreamDescriptor[]> {
    return Object.entries(STREAMS).map(([name, def]) => ({
      name,
      supportsIncremental: true,
      primaryKey: def.key,
      cursorField: def.cursorField,
    }));
  }

  async *sync<T = Record<string, unknown>>(
    request: SyncRequest,
  ): AsyncIterable<SyncBatch<T>> {
    const def = STREAMS[request.stream];
    if (!def) {
      throw new DataIntegrityError(`Unknown Zendesk stream: ${request.stream}`, {
        tenantId: request.context.tenantId,
        connectionId: request.context.connectionId,
        operation: `zendesk.sync.${request.stream}`,
      });
    }

    let state = decodeCursor(request.cursor);
    // First incremental run: start from epoch 0 unless caller provided one.
    if (state.cursor === undefined && state.startTime === undefined) {
      state = { startTime: request.mode === 'full' ? 0 : 0 };
    }

    let endOfStream = false;
    while (!endOfStream) {
      if (request.signal?.aborted) return;

      const { authHeaders } = await this.options.credentialProvider(request.context);
      const url = this.buildUrl(def.resource, state, request.pageSizeHint);

      const { value: response } = await executeWithRetry(
        async () => {
          const res = await this.options.http.request({
            method: 'GET',
            url,
            headers: { ...authHeaders, accept: 'application/json' },
            signal: request.signal,
          });
          const mapped = mapResponseToError(
            res,
            {
              tenantId: request.context.tenantId,
              connectionId: request.context.connectionId,
              operation: `zendesk.sync.${request.stream}`,
            },
            this.now(),
          );
          if (mapped) throw mapped;
          return JSON.parse(res.body) as ZendeskIncrementalResponse;
        },
        {
          onRetry: () => {
            /* observability hook; orchestrator logs via event rail */
          },
        },
      );

      const rawRecords = (response[def.resource as keyof ZendeskIncrementalResponse] ??
        []) as Array<Record<string, unknown>>;

      const records: SyncRecord<T>[] = rawRecords.map((raw) => {
        const id = String(raw[def.key]);
        return {
          stream: request.stream,
          id,
          data: raw as unknown as T,
          deleted: raw['status'] === 'deleted',
        };
      });

      endOfStream = response.end_of_stream === true;
      const nextState: ZendeskCursorState = response.after_cursor
        ? { cursor: response.after_cursor }
        : state;
      const nextCursor = encodeCursor(nextState);
      state = nextState;

      yield {
        records,
        nextCursor,
        hasMore: !endOfStream,
      };
    }
  }

  private buildUrl(resource: string, state: ZendeskCursorState, pageSize?: number): string {
    const base = `${zendeskBaseUrl(this.options.config)}/api/v2/incremental/${resource}/cursor.json`;
    const params = new URLSearchParams();
    if (state.cursor) {
      params.set('cursor', state.cursor);
    } else {
      params.set('start_time', String(state.startTime ?? 0));
    }
    const size = pageSize ?? this.options.defaultPageSize ?? 100;
    params.set('per_page', String(Math.min(size, 1000)));
    return `${base}?${params.toString()}`;
  }
}

export const __test__ = { encodeCursor, decodeCursor };
