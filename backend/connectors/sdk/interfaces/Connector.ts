import { ConnectorType, TenantContext } from './types';
import { ConnectorAuthenticator } from './ConnectorAuthenticator';
import { ConnectorSync } from './ConnectorSync';
import { ConnectorHealthCheck } from './ConnectorHealthCheck';
import { ConnectorLifecycle } from './ConnectorLifecycle';

/**
 * The top-level Connector interface.
 *
 * A connector is a composition of capability interfaces. This keeps each
 * concern independently testable while presenting a single registration unit
 * to the connector registry.
 *
 * `TCredential` is the connector's private credential shape; it never escapes
 * the connector except through the Secret Management Layer.
 */

export interface ConnectorMetadata {
  readonly type: ConnectorType;
  /** Human-friendly display name. */
  readonly displayName: string;
  /** Semantic version of the connector implementation. */
  readonly version: string;
  /** Provider documentation URL. */
  readonly docsUrl?: string;
  /** Capabilities advertised to the platform UI. */
  readonly capabilities: {
    readonly incrementalSync: boolean;
    readonly webhooks: boolean;
    readonly backfill: boolean;
  };
}

export interface Connector<TCredential = unknown> extends ConnectorLifecycle {
  readonly metadata: ConnectorMetadata;
  readonly auth: ConnectorAuthenticator<TCredential>;
  readonly sync: ConnectorSync;
  readonly health: ConnectorHealthCheck;

  /**
   * Optional validation of tenant-supplied configuration (e.g. subdomain)
   * before a connection is created. Throws ValidationError on bad input.
   */
  validateConfig?(context: TenantContext, config: Record<string, unknown>): Promise<void>;
}

/** Registry contract used by the platform to resolve connectors by type. */
export interface ConnectorRegistry {
  register(connector: Connector): void;
  get(type: ConnectorType): Connector;
  has(type: ConnectorType): boolean;
  list(): readonly ConnectorMetadata[];
}

export class InMemoryConnectorRegistry implements ConnectorRegistry {
  private readonly connectors = new Map<string, Connector>();

  register(connector: Connector): void {
    const key = connector.metadata.type as unknown as string;
    if (this.connectors.has(key)) {
      throw new Error(`Connector already registered: ${key}`);
    }
    this.connectors.set(key, connector);
  }

  get(type: ConnectorType): Connector {
    const key = type as unknown as string;
    const c = this.connectors.get(key);
    if (!c) {
      throw new Error(`No connector registered for type: ${key}`);
    }
    return c;
  }

  has(type: ConnectorType): boolean {
    return this.connectors.has(type as unknown as string);
  }

  list(): readonly ConnectorMetadata[] {
    return [...this.connectors.values()].map((c) => c.metadata);
  }
}
