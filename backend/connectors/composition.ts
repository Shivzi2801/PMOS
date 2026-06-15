/**
 * Composition root (example).
 *
 * Shows how the pieces wire together in production. This is illustrative — the
 * real wiring lives in the platform's bootstrap — but it compiles against the
 * actual interfaces and documents the intended dependency graph.
 *
 * The only things that change between dev and prod are the KMS provider
 * (LocalKmsProvider vs AwsKmsProvider) and the HTTP client (already real).
 * Everything else is identical, which is the point of the port/adapter design.
 */

import {
  ConnectionLifecycle,
  LifecycleEmitter,
  Logger,
  SyncOrchestrator,
  RecordSink,
  SyncStateStore,
  SyncRunStore,
  IdGenerator,
  TenantContext,
  asConnectorType,
} from './sdk';
import {
  SecretService,
  SecretCrypto,
  LocalKmsProvider,
  AwsKmsProvider,
  AwsKmsClientLike,
  PrismaSecretStore,
  PrismaConnectionStore,
  SecretAuditSink,
  PrismaLike,
} from './secrets';
import {
  ConnectorEventPublisher,
  ConnectorEventFactory,
} from './events';
import { FetchHttpClient, ZendeskConnector } from './zendesk';

export interface BuildOptions {
  readonly prisma: PrismaLike;
  readonly logger: Logger;
  readonly auditSink: SecretAuditSink;
  readonly eventPublisher: ConnectorEventPublisher;
  readonly ids: IdGenerator;
  readonly env: 'development' | 'production';
  /** Provided in production. */
  readonly awsKms?: AwsKmsClientLike;
  /** OAuth app credentials (platform-level, not tenant secrets). */
  readonly zendeskClientId: string;
  readonly zendeskClientSecret: string;
}

export interface ConnectorPlatform {
  readonly secretService: SecretService;
  readonly lifecycle: ConnectionLifecycle;
  buildZendeskConnector(subdomain: string): ZendeskConnector;
  buildOrchestrator(
    connector: ZendeskConnector,
    sink: RecordSink,
    stateStore: SyncStateStore,
    runStore: SyncRunStore,
  ): SyncOrchestrator;
}

export function buildPlatform(opts: BuildOptions): ConnectorPlatform {
  // --- KMS provider selection -------------------------------------------
  const kms =
    opts.env === 'production' && opts.awsKms
      ? new AwsKmsProvider(opts.awsKms, {
          aliasForTenant: (tenantId) => `alias/pmos/tenant/${tenantId}`,
        })
      : new LocalKmsProvider();

  // --- Secret service ----------------------------------------------------
  const crypto = new SecretCrypto(kms);
  const secretStore = new PrismaSecretStore(opts.prisma);
  const secretService = new SecretService(secretStore, crypto, opts.auditSink);

  // --- Lifecycle ---------------------------------------------------------
  const connectionStore = new PrismaConnectionStore(opts.prisma);
  const eventFactory = new ConnectorEventFactory({ eventId: () => opts.ids.eventId() });
  const emitter: LifecycleEmitter = {
    async connectorConnected(ctx: TenantContext, connectorType: string) {
      await opts.eventPublisher.publish(
        eventFactory.connected(
          {
            tenantId: ctx.tenantId,
            connectionId: ctx.connectionId,
            connectorType,
            correlationId: ctx.correlationId,
            actor: ctx.actor,
            occurredAt: new Date().toISOString(),
          },
          [],
        ),
      );
    },
    async connectorDisconnected(ctx: TenantContext, connectorType: string, reason: string) {
      await opts.eventPublisher.publish(
        eventFactory.disconnected(
          {
            tenantId: ctx.tenantId,
            connectionId: ctx.connectionId,
            connectorType,
            correlationId: ctx.correlationId,
            actor: ctx.actor,
            occurredAt: new Date().toISOString(),
          },
          reason,
        ),
      );
    },
  };
  const lifecycle = new ConnectionLifecycle(connectionStore, emitter, opts.logger);

  // --- Zendesk connector factory ----------------------------------------
  const http = new FetchHttpClient();

  function buildZendeskConnector(subdomain: string): ZendeskConnector {
    return new ZendeskConnector({
      clientId: opts.zendeskClientId,
      clientSecret: opts.zendeskClientSecret,
      config: { subdomain },
      http,
      // Credential provider resolves the KMS-enveloped OAuth secret per call.
      credentialProvider: async (ctx) => {
        const raw = await secretService.read(
          { tenantId: ctx.tenantId, connectionId: ctx.connectionId, name: 'oauth' },
          { actor: ctx.actor, correlationId: ctx.correlationId },
        );
        try {
          const credential = JSON.parse(raw.toString('utf8'));
          return {
            credential,
            authHeaders: { authorization: `Bearer ${credential.accessToken}` },
          };
        } finally {
          raw.fill(0);
        }
      },
    });
  }

  function buildOrchestrator(
    connector: ZendeskConnector,
    sink: RecordSink,
    stateStore: SyncStateStore,
    runStore: SyncRunStore,
  ): SyncOrchestrator {
    return new SyncOrchestrator({
      connector,
      sink,
      stateStore,
      runStore,
      publisher: opts.eventPublisher,
      ids: opts.ids,
    });
  }

  return { secretService, lifecycle, buildZendeskConnector, buildOrchestrator };
}
