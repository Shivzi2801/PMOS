import { TenantContext } from './types';

/**
 * Health check interface.
 *
 * Health checks are used both at connection time (validate the credential and
 * reachability before marking a connection ACTIVE) and on a schedule (detect
 * silent credential revocation, provider outages, scope changes).
 */

export type HealthStatus = 'healthy' | 'degraded' | 'unhealthy';

export interface HealthCheckResult {
  readonly status: HealthStatus;
  /** Human-readable summary, safe to surface in tenant UI. */
  readonly message: string;
  /** Round-trip latency in ms for the probe call, if measured. */
  readonly latencyMs?: number;
  /** Structured, non-secret diagnostics. */
  readonly details?: Record<string, unknown>;
  readonly checkedAt: string; // ISO-8601
}

export interface ConnectorHealthCheck {
  /**
   * Probe the provider using the tenant's current credential. Should be cheap
   * and read-only (e.g. GET current user). MUST NOT mutate provider state.
   */
  check(context: TenantContext): Promise<HealthCheckResult>;
}
