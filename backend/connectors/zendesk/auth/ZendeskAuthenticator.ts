import { createHash, randomBytes } from 'node:crypto';
import {
  AuthorizeUrlRequest,
  AuthorizeUrlResult,
  ConnectorAuthenticator,
  CredentialState,
  ExchangeCodeRequest,
  RefreshRequest,
} from '../../sdk/interfaces/ConnectorAuthenticator';
import { TenantContext } from '../../sdk/interfaces/types';
import {
  AuthenticationError,
  ConfigurationError,
  toConnectorError,
} from '../../sdk/errors/ConnectorError';
import { HttpClient, mapResponseToError } from '../http';
import { ZendeskConfig, ZendeskOAuthCredential, zendeskOAuthBaseUrl } from '../types';

/**
 * Zendesk OAuth 2.0 authenticator (authorization code flow with PKCE).
 *
 * Flow:
 *   1. buildAuthorizeUrl -> redirect the tenant admin to Zendesk consent.
 *      We generate a PKCE verifier; the caller persists it alongside `state`.
 *   2. exchangeCode -> swap the returned code for tokens. The resulting
 *      credential is handed to SecretService for KMS-enveloped storage by the
 *      orchestrator; this class does not persist anything.
 *   3. refresh -> exchange a refresh token for a new access token.
 *
 * Client id/secret are platform-level (per Zendesk OAuth app), injected via
 * options, NOT tenant secrets. Tenant secrets are the resulting tokens.
 */

export interface ZendeskOAuthOptions {
  readonly clientId: string;
  readonly clientSecret: string;
  readonly config: ZendeskConfig;
  readonly http: HttpClient;
  /** Injectable RNG/time for deterministic tests. */
  readonly random?: () => Buffer;
  readonly now?: () => number;
}

interface ZendeskTokenResponse {
  access_token: string;
  token_type: string;
  scope: string;
  refresh_token?: string;
  expires_in?: number;
}

export class ZendeskAuthenticator implements ConnectorAuthenticator<ZendeskOAuthCredential> {
  readonly method = 'oauth2' as const;
  readonly defaultScopes = ['read', 'tickets:read', 'users:read'];

  constructor(private readonly options: ZendeskOAuthOptions) {
    if (!options.clientId || !options.clientSecret) {
      throw new ConfigurationError('Zendesk OAuth clientId/clientSecret required');
    }
  }

  private rand(bytes: number): Buffer {
    return this.options.random ? this.options.random() : randomBytes(bytes);
  }

  private now(): number {
    return this.options.now ? this.options.now() : Date.now();
  }

  async buildAuthorizeUrl(request: AuthorizeUrlRequest): Promise<AuthorizeUrlResult> {
    const codeVerifier = this.rand(32).toString('base64url');
    const codeChallenge = createHash('sha256').update(codeVerifier).digest('base64url');
    const scopes = request.scopes.length ? request.scopes : this.defaultScopes;

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: this.options.clientId,
      redirect_uri: request.redirectUri,
      scope: scopes.join(' '),
      state: request.state,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
    });

    const url = `${zendeskOAuthBaseUrl(this.options.config)}/oauth/authorizations/new?${params.toString()}`;
    return { url, codeVerifier };
  }

  async exchangeCode(
    request: ExchangeCodeRequest<ZendeskOAuthCredential>,
  ): Promise<ZendeskOAuthCredential> {
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      code: request.code,
      client_id: this.options.clientId,
      client_secret: this.options.clientSecret,
      redirect_uri: request.redirectUri,
      ...(request.codeVerifier ? { code_verifier: request.codeVerifier } : {}),
    });

    return this.tokenRequest(request.context, body);
  }

  async refresh(request: RefreshRequest<ZendeskOAuthCredential>): Promise<ZendeskOAuthCredential> {
    if (!request.current.refreshToken) {
      throw new AuthenticationError('No refresh token available; reauthorization required', {
        tenantId: request.context.tenantId,
        connectionId: request.context.connectionId,
      });
    }
    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      refresh_token: request.current.refreshToken,
      client_id: this.options.clientId,
      client_secret: this.options.clientSecret,
    });
    return this.tokenRequest(request.context, body);
  }

  private async tokenRequest(
    context: TenantContext,
    body: URLSearchParams,
  ): Promise<ZendeskOAuthCredential> {
    const url = `${zendeskOAuthBaseUrl(this.options.config)}/oauth/tokens`;
    try {
      const res = await this.options.http.request({
        method: 'POST',
        url,
        headers: {
          'content-type': 'application/x-www-form-urlencoded',
          accept: 'application/json',
        },
        body: body.toString(),
      });
      const mapped = mapResponseToError(
        res,
        { tenantId: context.tenantId, connectionId: context.connectionId, operation: 'zendesk.oauth.token' },
        this.now(),
      );
      if (mapped) throw mapped;

      const parsed = JSON.parse(res.body) as ZendeskTokenResponse;
      const expiresAt =
        typeof parsed.expires_in === 'number'
          ? this.now() + parsed.expires_in * 1000
          : undefined;
      return {
        accessToken: parsed.access_token,
        refreshToken: parsed.refresh_token,
        tokenType: parsed.token_type ?? 'bearer',
        scope: parsed.scope ?? '',
        expiresAt,
      };
    } catch (err) {
      throw toConnectorError(err, {
        tenantId: context.tenantId,
        connectionId: context.connectionId,
        operation: 'zendesk.oauth.token',
      });
    }
  }

  inspect(credential: ZendeskOAuthCredential): CredentialState {
    const expiresAt = credential.expiresAt;
    const valid = !!credential.accessToken && (expiresAt === undefined || expiresAt > this.now());
    return {
      valid,
      expiresAt,
      refreshable: !!credential.refreshToken,
    };
  }

  async revoke(context: TenantContext, credential: ZendeskOAuthCredential): Promise<void> {
    // Zendesk supports DELETE /api/v2/oauth/tokens/{id}; without the token id we
    // best-effort hit the current-token revocation endpoint. Failures here are
    // swallowed: local secret deletion proceeds regardless.
    try {
      await this.options.http.request({
        method: 'DELETE',
        url: `${zendeskOAuthBaseUrl(this.options.config)}/api/v2/oauth/tokens/current.json`,
        headers: this.authorizeRequest(credential),
      });
    } catch {
      // Intentionally ignored — revocation is best-effort.
    }
  }

  authorizeRequest(credential: ZendeskOAuthCredential): Record<string, string> {
    if (!credential.accessToken) {
      throw new AuthenticationError('Missing access token');
    }
    return { authorization: `Bearer ${credential.accessToken}` };
  }
}
