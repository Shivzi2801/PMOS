import { SecretEnvelope } from './SecretCrypto';
import { PrismaLike, PrismaTxLike, withTenant } from './prisma';
import { SecretRef, SecretStore, SecretStatus, StoredSecret } from '../model/SecretModel';

/**
 * PrismaSecretStore
 *
 * Persists KMS-enveloped secrets with strict versioning under RLS.
 *
 * Versioning rule (atomic within one transaction):
 *   1. Lock the logical secret rows (SELECT ... FOR UPDATE on the (tenant,
 *      connection, name) group) to serialize concurrent rotations.
 *   2. Mark any current ACTIVE version SUPERSEDED.
 *   3. Insert the new version as ACTIVE with version = max(version)+1.
 *
 * The partial unique index `connector_secret_one_active` is the backstop: even
 * under a race the DB rejects a second ACTIVE row.
 */

interface SecretRow {
  id: string;
  tenantId: string;
  connectionId: string;
  name: string;
  version: number;
  status: SecretStatus;
  ciphertext: Buffer;
  iv: Buffer;
  authTag: Buffer;
  wrappedDek: Buffer;
  keyId: string;
  expiresAt: bigint | null;
  createdAt: Date;
  rotatedAt: Date | null;
}

function rowToStored(r: SecretRow): StoredSecret {
  const envelope: SecretEnvelope = {
    ciphertext: Buffer.from(r.ciphertext),
    iv: Buffer.from(r.iv),
    authTag: Buffer.from(r.authTag),
    wrappedDek: Buffer.from(r.wrappedDek),
    keyId: r.keyId,
  };
  return {
    id: r.id,
    tenantId: r.tenantId,
    connectionId: r.connectionId,
    name: r.name,
    version: r.version,
    status: r.status,
    envelope,
    createdAt: r.createdAt,
    rotatedAt: r.rotatedAt ?? undefined,
    expiresAt: r.expiresAt === null ? undefined : Number(r.expiresAt),
  };
}

export class PrismaSecretStore implements SecretStore {
  constructor(private readonly prisma: PrismaLike) {}

  async getActive(ref: SecretRef): Promise<StoredSecret | null> {
    return withTenant(this.prisma, ref.tenantId, async (tx) => {
      const rows = await tx.$queryRawUnsafe<SecretRow[]>(
        `SELECT * FROM "connector_secret"
         WHERE "tenantId" = $1 AND "connectionId" = $2 AND "name" = $3 AND "status" = 'ACTIVE'
         LIMIT 1`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
      );
      return rows[0] ? rowToStored(rows[0]) : null;
    });
  }

  async getVersion(ref: SecretRef, version: number): Promise<StoredSecret | null> {
    return withTenant(this.prisma, ref.tenantId, async (tx) => {
      const rows = await tx.$queryRawUnsafe<SecretRow[]>(
        `SELECT * FROM "connector_secret"
         WHERE "tenantId" = $1 AND "connectionId" = $2 AND "name" = $3 AND "version" = $4
         LIMIT 1`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
        version,
      );
      return rows[0] ? rowToStored(rows[0]) : null;
    });
  }

  async listVersions(ref: SecretRef): Promise<readonly StoredSecret[]> {
    return withTenant(this.prisma, ref.tenantId, async (tx) => {
      const rows = await tx.$queryRawUnsafe<SecretRow[]>(
        `SELECT * FROM "connector_secret"
         WHERE "tenantId" = $1 AND "connectionId" = $2 AND "name" = $3
         ORDER BY "version" DESC`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
      );
      return rows.map(rowToStored);
    });
  }

  async putNewVersion(input: {
    ref: SecretRef;
    envelope: SecretEnvelope;
    expiresAt?: number;
  }): Promise<StoredSecret> {
    const { ref, envelope, expiresAt } = input;
    return withTenant(this.prisma, ref.tenantId, async (tx) => {
      // Lock existing versions to serialize rotation.
      await tx.$queryRawUnsafe(
        `SELECT "id" FROM "connector_secret"
         WHERE "tenantId" = $1 AND "connectionId" = $2 AND "name" = $3
         FOR UPDATE`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
      );

      // Supersede current ACTIVE.
      await tx.$executeRawUnsafe(
        `UPDATE "connector_secret" SET "status" = 'SUPERSEDED', "rotatedAt" = now()
         WHERE "tenantId" = $1 AND "connectionId" = $2 AND "name" = $3 AND "status" = 'ACTIVE'`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
      );

      // Compute next version.
      const maxRows = await tx.$queryRawUnsafe<Array<{ max: number | null }>>(
        `SELECT MAX("version") AS max FROM "connector_secret"
         WHERE "tenantId" = $1 AND "connectionId" = $2 AND "name" = $3`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
      );
      const nextVersion = (maxRows[0]?.max ?? 0) + 1;

      const inserted = await tx.$queryRawUnsafe<SecretRow[]>(
        `INSERT INTO "connector_secret"
          ("tenantId","connectionId","name","version","status",
           "ciphertext","iv","authTag","wrappedDek","keyId","expiresAt")
         VALUES ($1,$2,$3,$4,'ACTIVE',$5,$6,$7,$8,$9,$10)
         RETURNING *`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
        nextVersion,
        envelope.ciphertext,
        envelope.iv,
        envelope.authTag,
        envelope.wrappedDek,
        envelope.keyId,
        expiresAt === undefined ? null : BigInt(expiresAt),
      );
      return rowToStored(inserted[0]);
    });
  }

  async revokeAll(ref: SecretRef): Promise<number> {
    return withTenant(this.prisma, ref.tenantId, async (tx) =>
      tx.$executeRawUnsafe(
        `UPDATE "connector_secret" SET "status" = 'REVOKED'
         WHERE "tenantId" = $1 AND "connectionId" = $2 AND "name" = $3 AND "status" <> 'REVOKED'`,
        ref.tenantId,
        ref.connectionId,
        ref.name,
      ),
    );
  }

  async deleteAll(ref: Omit<SecretRef, 'name'>): Promise<number> {
    return withTenant(this.prisma, ref.tenantId, async (tx) =>
      tx.$executeRawUnsafe(
        `DELETE FROM "connector_secret"
         WHERE "tenantId" = $1 AND "connectionId" = $2`,
        ref.tenantId,
        ref.connectionId,
      ),
    );
  }

  async withLock<T>(ref: SecretRef, fn: () => Promise<T>): Promise<T> {
    // Advisory lock keyed by a hash of the ref, scoped to the tenant tx.
    return withTenant(this.prisma, ref.tenantId, async (tx) => {
      await lockRef(tx, ref);
      return fn();
    });
  }
}

async function lockRef(tx: PrismaTxLike, ref: SecretRef): Promise<void> {
  // pg_advisory_xact_lock takes a bigint; derive a stable key from the ref.
  const key = `${ref.tenantId}:${ref.connectionId}:${ref.name}`;
  await tx.$executeRawUnsafe(
    `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
    key,
  );
}
