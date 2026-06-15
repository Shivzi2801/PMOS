/**
 * Prisma access ports.
 *
 * We do not import @prisma/client at the SDK layer to keep the package
 * dependency-light and unit-testable. Instead we declare the minimal client
 * shape we use. The production composition wires the real PrismaClient.
 *
 * RLS contract: every tenant-scoped query MUST run inside a transaction that
 * first sets the `app.tenant_id` GUC. `withTenant` encapsulates this so callers
 * cannot forget it. Using `set_config(..., true)` makes the setting
 * transaction-local, so it is automatically cleared at commit/rollback and
 * cannot leak across pooled connections.
 */

export interface PrismaLike {
  $executeRawUnsafe(query: string, ...values: unknown[]): Promise<number>;
  $queryRawUnsafe<T = unknown>(query: string, ...values: unknown[]): Promise<T>;
  $transaction<T>(fn: (tx: PrismaTxLike) => Promise<T>): Promise<T>;
}

export interface PrismaTxLike {
  $executeRawUnsafe(query: string, ...values: unknown[]): Promise<number>;
  $queryRawUnsafe<T = unknown>(query: string, ...values: unknown[]): Promise<T>;
  // Model delegates are accessed dynamically by the concrete stores.
  [model: string]: unknown;
}

/**
 * Run `fn` inside a transaction with `app.tenant_id` set for RLS. Always use
 * this for any tenant-scoped DB work.
 */
export async function withTenant<T>(
  prisma: PrismaLike,
  tenantId: string,
  fn: (tx: PrismaTxLike) => Promise<T>,
): Promise<T> {
  return prisma.$transaction(async (tx) => {
    // Transaction-local GUC; cleared automatically at end of tx.
    await tx.$executeRawUnsafe('SELECT set_config($1, $2, true)', 'app.tenant_id', tenantId);
    return fn(tx);
  });
}
