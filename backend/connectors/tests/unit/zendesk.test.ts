import { describe, it, expect } from 'vitest';
import { mapResponseToError, parseRetryAfterMs } from '../../zendesk/http';
import { ZendeskAuthenticator } from '../../zendesk/auth/ZendeskAuthenticator';
import { ZendeskWebhookVerifier, __test__ as webhookTest } from '../../zendesk/webhook/ZendeskWebhook';
import { StubHttp, json } from '../fakes';
import { TenantContext, asConnectionId, asTenantId } from '../../sdk/interfaces/types';
import { ConnectorErrorCategory } from '../../sdk/errors/ConnectorError';

const ctx: TenantContext = {
  tenantId: asTenantId('t1'),
  connectionId: asConnectionId('c1'),
  actor: 'system',
  correlationId: 'corr',
};

describe('zendesk http error mapping', () => {
  it('maps 429 to RateLimitError with Retry-After', () => {
    const err = mapResponseToError(
      { status: 429, headers: { 'retry-after': '3' }, body: '' },
      { operation: 'x' },
      0,
    );
    expect(err).toMatchObject({ category: ConnectorErrorCategory.RateLimited, retryAfterMs: 3000 });
  });
  it('maps 401 -> Authentication, 403 -> Authorization', () => {
    expect(mapResponseToError({ status: 401, headers: {}, body: '' }, {})).toMatchObject({
      category: ConnectorErrorCategory.Authentication,
    });
    expect(mapResponseToError({ status: 403, headers: {}, body: '' }, {})).toMatchObject({
      category: ConnectorErrorCategory.Authorization,
    });
  });
  it('maps 5xx to Transient and other 4xx to Permanent', () => {
    expect(mapResponseToError({ status: 503, headers: {}, body: '' }, {})).toMatchObject({
      category: ConnectorErrorCategory.Transient,
    });
    expect(mapResponseToError({ status: 404, headers: {}, body: '' }, {})).toMatchObject({
      category: ConnectorErrorCategory.Permanent,
    });
  });
  it('returns null for 2xx', () => {
    expect(mapResponseToError({ status: 200, headers: {}, body: '{}' }, {})).toBeNull();
  });
  it('parses Retry-After seconds and dates', () => {
    expect(parseRetryAfterMs('5', 0)).toBe(5000);
    expect(parseRetryAfterMs(undefined, 0)).toBe(1000);
    const future = new Date(10_000).toUTCString();
    expect(parseRetryAfterMs(future, 0)).toBeGreaterThanOrEqual(0);
  });
});

describe('ZendeskAuthenticator', () => {
  function make(http: StubHttp, now = () => 1_000_000) {
    return new ZendeskAuthenticator({
      clientId: 'cid',
      clientSecret: 'csec',
      config: { subdomain: 'acme' },
      http,
      now,
    });
  }

  it('builds a PKCE authorize URL with challenge', async () => {
    const auth = make(new StubHttp());
    const res = await auth.buildAuthorizeUrl({
      context: ctx,
      redirectUri: 'https://app/cb',
      state: 'st',
      scopes: ['read'],
    });
    expect(res.url).toContain('acme.zendesk.com/oauth/authorizations/new');
    expect(res.url).toContain('code_challenge_method=S256');
    expect(res.url).toContain('state=st');
    expect(res.codeVerifier).toBeTruthy();
  });

  it('exchanges a code for a credential with computed expiry', async () => {
    const http = new StubHttp().enqueue(
      json(200, { access_token: 'AT', token_type: 'bearer', scope: 'read', refresh_token: 'RT', expires_in: 100 }),
    );
    const auth = make(http);
    const cred = await auth.exchangeCode({ context: ctx, code: 'code', redirectUri: 'https://app/cb' });
    expect(cred.accessToken).toBe('AT');
    expect(cred.refreshToken).toBe('RT');
    expect(cred.expiresAt).toBe(1_000_000 + 100_000);
  });

  it('refresh throws when there is no refresh token', async () => {
    const auth = make(new StubHttp());
    await expect(
      auth.refresh({ context: ctx, current: { accessToken: 'AT', tokenType: 'bearer', scope: 'read' } }),
    ).rejects.toMatchObject({ category: ConnectorErrorCategory.Authentication });
  });

  it('inspect reports validity and refreshability', () => {
    const auth = make(new StubHttp());
    expect(auth.inspect({ accessToken: 'AT', tokenType: 'bearer', scope: '', expiresAt: 2_000_000 })).toMatchObject({
      valid: true,
      refreshable: false,
    });
    expect(auth.inspect({ accessToken: 'AT', refreshToken: 'r', tokenType: 'bearer', scope: '', expiresAt: 1 })).toMatchObject({
      valid: false,
      refreshable: true,
    });
  });

  it('authorizeRequest sets a Bearer header', () => {
    const auth = make(new StubHttp());
    expect(auth.authorizeRequest({ accessToken: 'AT', tokenType: 'bearer', scope: '' })).toEqual({
      authorization: 'Bearer AT',
    });
  });
});

describe('ZendeskWebhookVerifier', () => {
  const secret = 'whsec';
  const verifier = new ZendeskWebhookVerifier();

  function delivery(body: string, ts: string) {
    return { rawBody: body, signature: webhookTest.computeSignature(secret, ts, body), timestamp: ts };
  }

  it('accepts a correctly signed, timely delivery', () => {
    const now = Date.parse('2026-06-15T00:00:00Z');
    const ts = new Date(now).toISOString();
    const body = JSON.stringify({ type: 'ticket.created', id: 5 });
    const event = verifier.verify(ctx, delivery(body, ts), secret, { now: () => now });
    expect(event.type).toBe('ticket.created');
  });

  it('rejects a tampered signature', () => {
    const now = Date.parse('2026-06-15T00:00:00Z');
    const ts = new Date(now).toISOString();
    const d = delivery('{"type":"x"}', ts);
    const bad = { ...d, signature: d.signature.slice(0, -2) + 'aa' };
    expect(() => verifier.verify(ctx, bad, secret, { now: () => now })).toThrowError();
  });

  it('rejects a stale timestamp (replay protection)', () => {
    const now = Date.parse('2026-06-15T00:00:00Z');
    const staleTs = new Date(now - 10 * 60 * 1000).toISOString();
    const body = '{"type":"x"}';
    expect(() => verifier.verify(ctx, delivery(body, staleTs), secret, { now: () => now })).toThrowError();
  });

  it('rejects an invalid JSON body after signature passes', () => {
    const now = Date.parse('2026-06-15T00:00:00Z');
    const ts = new Date(now).toISOString();
    const body = 'not-json';
    expect(() => verifier.verify(ctx, delivery(body, ts), secret, { now: () => now })).toThrowError();
  });
});
