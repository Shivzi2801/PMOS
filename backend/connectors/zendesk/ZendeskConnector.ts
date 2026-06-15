import { Connector, ConnectorMetadata } from '../sdk/interfaces/Connector';
import { LifecycleHooks } from '../sdk/interfaces/ConnectorLifecycle';
import { TenantContext, asConnectorType } from '../sdk/interfaces/types';
import { ValidationError } from '../sdk/errors/ConnectorError';
import { ZendeskAuthenticator } from './auth/ZendeskAuthenticator';
import { ZendeskSync, CredentialProvider } from './sync/ZendeskSync';
import { ZendeskHealthCheck } from './ZendeskHealthCheck';
import { HttpClient } from './http';
import { ZendeskConfig, ZendeskOAuthCredential, isValidSubdomain } from './types';

/**
 * ZendeskConnector
 *
 * The reference connector for the SDK. It wires the OAuth authenticator,
 * incremental sync, and health check into a single registrable unit. All
 * provider I/O flows through the injected HttpClient; all credential resolution
 * flows through the injected CredentialProvider (backed by SecretService in
 * production). This keeps the connector free of any direct secret-store or
 * network coupling and therefore fully unit-testable.
 */

export const ZENDESK_CONNECTOR_TYPE = asConnectorType('zendesk');

export interface ZendeskConnectorDeps {
  readonly clientId: string;
  readonly clientSecret: string;
  readonly config: ZendeskConfig;
  readonly http: HttpClient;
  readonly credentialProvider: CredentialProvider;
  readonly hooks?: LifecycleHooks;
  readonly now?: () => number;
}

export class ZendeskConnector implements Connector<ZendeskOAuthCredential> {
  readonly metadata: ConnectorMetadata = {
    type: ZENDESK_CONNECTOR_TYPE,
    displayName: 'Zendesk',
    version: '1.0.0',
    docsUrl: 'https://developer.zendesk.com/api-reference/',
    capabilities: {
      incrementalSync: true,
      webhooks: true,
      backfill: true,
    },
  };

  readonly auth: ZendeskAuthenticator;
  readonly sync: ZendeskSync;
  readonly health: ZendeskHealthCheck;
  readonly hooks?: LifecycleHooks;

  constructor(deps: ZendeskConnectorDeps) {
    this.auth = new ZendeskAuthenticator({
      clientId: deps.clientId,
      clientSecret: deps.clientSecret,
      config: deps.config,
      http: deps.http,
      now: deps.now,
    });
    this.sync = new ZendeskSync({
      config: deps.config,
      http: deps.http,
      credentialProvider: deps.credentialProvider,
      now: deps.now,
    });
    this.health = new ZendeskHealthCheck(
      deps.config,
      deps.http,
      deps.credentialProvider,
      deps.now,
    );
    this.hooks = deps.hooks;
  }

  async validateConfig(context: TenantContext, config: Record<string, unknown>): Promise<void> {
    const subdomain = config['subdomain'];
    if (typeof subdomain !== 'string' || !isValidSubdomain(subdomain)) {
      throw new ValidationError('Invalid Zendesk subdomain', {
        tenantId: context.tenantId,
        connectionId: context.connectionId,
        operation: 'zendesk.validateConfig',
        detail: { subdomain: typeof subdomain === 'string' ? subdomain : '<non-string>' },
      });
    }
  }
}
