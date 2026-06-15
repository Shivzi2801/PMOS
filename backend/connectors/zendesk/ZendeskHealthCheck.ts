import {
  ConnectorHealthCheck,
  HealthCheckResult,
} from '../sdk/interfaces/ConnectorHealthCheck';
import { TenantContext } from '../sdk/interfaces/types';
import { mapResponseToError } from './http';
import { HttpClient } from './http';
import { CredentialProvider } from './sync/ZendeskSync';
import { ZendeskConfig, zendeskBaseUrl } from './types';

/**
 * Zendesk health check.
 *
 * Probes GET /api/v2/users/me.json — a cheap, read-only call that validates
 * both reachability and credential validity. Maps the result into the SDK
 * health status:
 *   - 2xx           -> healthy
 *   - 401/403       -> unhealthy (credential problem; tenant action needed)
 *   - 429/5xx       -> degraded (transient; will likely recover)
 */
export class ZendeskHealthCheck implements ConnectorHealthCheck {
  constructor(
    private readonly config: ZendeskConfig,
    private readonly http: HttpClient,
    private readonly credentialProvider: CredentialProvider,
    private readonly now: () => number = () => Date.now(),
  ) {}

  async check(context: TenantContext): Promise<HealthCheckResult> {
    const startedAt = this.now();
    const checkedAt = new Date(startedAt).toISOString();
    try {
      const { authHeaders } = await this.credentialProvider(context);
      const res = await this.http.request({
        method: 'GET',
        url: `${zendeskBaseUrl(this.config)}/api/v2/users/me.json`,
        headers: { ...authHeaders, accept: 'application/json' },
      });
      const latencyMs = this.now() - startedAt;
      const err = mapResponseToError(
        res,
        { tenantId: context.tenantId, connectionId: context.connectionId, operation: 'zendesk.health' },
        this.now(),
      );
      if (!err) {
        return { status: 'healthy', message: 'Zendesk reachable and credential valid', latencyMs, checkedAt };
      }
      if (res.status === 401 || res.status === 403) {
        return {
          status: 'unhealthy',
          message: 'Zendesk credential invalid or insufficient scope',
          latencyMs,
          details: { httpStatus: res.status },
          checkedAt,
        };
      }
      return {
        status: 'degraded',
        message: 'Zendesk transiently unavailable',
        latencyMs,
        details: { httpStatus: res.status },
        checkedAt,
      };
    } catch (err) {
      return {
        status: 'degraded',
        message: 'Zendesk health probe failed',
        latencyMs: this.now() - startedAt,
        details: { error: String(err) },
        checkedAt,
      };
    }
  }
}
