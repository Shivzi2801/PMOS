import { describe, it, expect } from 'vitest';
import { Connector } from '../../sdk/interfaces/Connector';
import { TenantContext, asConnectionId, asTenantId } from '../../sdk/interfaces/types';
import { ZendeskConnector } from '../../zendesk/ZendeskConnector';
import { StubHttp, json } from '../fakes';

/**
 * Connector contract suite.
 *
 * This is a reusable conformance suite: any connector implementation can be run
 * through `runConnectorContract` to verify it honors the SDK contracts. Here we
 * run it against the Zendesk reference connector. New connectors added in later
 * waves should import and invoke this same suite.
 */

const ctx: TenantContext = {
  tenantId: asTenantId('t1'),
  connectionId: asConnectionId('c1'),
  actor: 'system',
  correlationId: 'corr',
};

export function runConnectorContract(name: string, build: () => Connector): void {
  describe(`connector contract: ${name}`, () => {
    it('exposes complete metadata', () => {
      const c = build();
      expect(c.metadata.type).toBeTruthy();
      expect(c.metadata.displayName).toBeTruthy();
      expect(c.metadata.version).toMatch(/\d+\.\d+\.\d+/);
      expect(typeof c.metadata.capabilities.incrementalSync).toBe('boolean');
    });

    it('declares an auth method and default scopes', () => {
      const c = build();
      expect(['oauth2', 'api_key', 'basic', 'jwt']).toContain(c.auth.method);
      expect(Array.isArray(c.auth.defaultScopes)).toBe(true);
    });

    it('lists at least one stream with a primary key', async () => {
      const c = build();
      const streams = await c.sync.listStreams(ctx);
      expect(streams.length).toBeGreaterThan(0);
      for (const s of streams) {
        expect(s.name).toBeTruthy();
        expect(s.primaryKey).toBeTruthy();
      }
    });

    it('authorizeRequest produces headers from a credential', () => {
      const c = build();
      // Use a representative credential shape via the authenticator.
      const headers = (c.auth as { authorizeRequest: (cred: unknown) => Record<string, string> }).authorizeRequest({
        accessToken: 'AT',
        tokenType: 'bearer',
        scope: '',
      });
      expect(Object.keys(headers).length).toBeGreaterThan(0);
    });

    it('inspect classifies an expired credential as invalid', () => {
      const c = build();
      const state = (c.auth as { inspect: (cred: unknown) => { valid: boolean } }).inspect({
        accessToken: 'AT',
        tokenType: 'bearer',
        scope: '',
        expiresAt: 1, // far in the past
      });
      expect(state.valid).toBe(false);
    });
  });
}

function buildZendesk(): Connector {
  const http = new StubHttp().onRequest(() => json(200, { user: { id: 1 } }));
  return new ZendeskConnector({
    clientId: 'cid',
    clientSecret: 'csec',
    config: { subdomain: 'acme' },
    http,
    credentialProvider: async () => ({
      credential: { accessToken: 'AT', tokenType: 'bearer', scope: 'read' },
      authHeaders: { authorization: 'Bearer AT' },
    }),
    now: () => 1_000_000,
  }) as unknown as Connector;
}

runConnectorContract('zendesk', buildZendesk);

describe('zendesk config validation', () => {
  it('rejects an invalid subdomain', async () => {
    const c = buildZendesk() as unknown as ZendeskConnector;
    await expect(c.validateConfig(ctx, { subdomain: 'bad subdomain!' })).rejects.toBeTruthy();
  });
  it('accepts a valid subdomain', async () => {
    const c = buildZendesk() as unknown as ZendeskConnector;
    await expect(c.validateConfig(ctx, { subdomain: 'acme' })).resolves.toBeUndefined();
  });
});
