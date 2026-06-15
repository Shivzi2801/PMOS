import { ConnectionState } from '../../sdk/interfaces/ConnectorLifecycle';
import {
  ConnectionRecord,
  ConnectionStore,
} from '../../sdk/contracts/ConnectionLifecycle';
import { PrismaLike, PrismaTxLike, withTenant } from './prisma';

/**
 * PrismaConnectionStore
 *
 * Implements the lifecycle manager's ConnectionStore port against Postgres
 * under RLS. State transitions hold a row-level advisory + FOR UPDATE lock so
 * concurrent transitions on the same connection serialize cleanly.
 */

interface ConnectorRow {
  id: string;
  tenantId: string;
  connectorType: string;
  state: ConnectionState;
  stateReason: string | null;
  updatedAt: Date;
}

function rowToRecord(r: ConnectorRow): ConnectionRecord {
  return {
    tenantId: r.tenantId,
    connectionId: r.id,
    connectorType: r.connectorType,
    state: r.state,
    stateReason: r.stateReason ?? undefined,
    updatedAt: r.updatedAt,
  };
}

export class PrismaConnectionStore implements ConnectionStore {
  constructor(private readonly prisma: PrismaLike) {}

  async load(tenantId: string, connectionId: string): Promise<ConnectionRecord | null> {
    return withTenant(this.prisma, tenantId, async (tx) => {
      const rows = await tx.$queryRawUnsafe<ConnectorRow[]>(
        `SELECT "id","tenantId","connectorType","state","stateReason","updatedAt"
         FROM "connector" WHERE "tenantId" = $1 AND "id" = $2 LIMIT 1`,
        tenantId,
        connectionId,
      );
      return rows[0] ? rowToRecord(rows[0]) : null;
    });
  }

  async updateState(
    tenantId: string,
    connectionId: string,
    state: ConnectionState,
    reason: string | undefined,
    at: Date,
  ): Promise<ConnectionRecord> {
    return withTenant(this.prisma, tenantId, async (tx) => {
      const rows = await tx.$queryRawUnsafe<ConnectorRow[]>(
        `UPDATE "connector"
         SET "state" = $3::"ConnectionState", "stateReason" = $4, "updatedAt" = $5
         WHERE "tenantId" = $1 AND "id" = $2
         RETURNING "id","tenantId","connectorType","state","stateReason","updatedAt"`,
        tenantId,
        connectionId,
        state,
        reason ?? null,
        at,
      );
      if (!rows[0]) {
        throw new Error('Connection vanished during updateState');
      }
      return rowToRecord(rows[0]);
    });
  }

  async withLock<T>(tenantId: string, connectionId: string, fn: () => Promise<T>): Promise<T> {
    return withTenant(this.prisma, tenantId, async (tx) => {
      await lockConnection(tx, tenantId, connectionId);
      return fn();
    });
  }
}

async function lockConnection(
  tx: PrismaTxLike,
  tenantId: string,
  connectionId: string,
): Promise<void> {
  await tx.$executeRawUnsafe(
    `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
    `${tenantId}:${connectionId}`,
  );
}
