import { TenantContext } from './types';
import { ConnectorError } from '../errors/ConnectorError';

/**
 * Authentication interface.
 *
 * Connectors declare how they obtain, refresh, and revoke credentials. The
 * SDK never sees raw secret material in the clear longer than necessary:
 * credentials are resolved from the Secret Management Layer at call time and
 * passed transiently.
 *
 * The generic `TCredential` is the connector-specific shape (e.g. an OAuth
 * token bundle). It is always treated as sensitive and never logged.
 */

export type AuthMethod = 'oauth2' | 'api_key' | 'basic' | 'jwt';

/** Marker the connector returns to indicate credential validity at a point in time. */
export interface CredentialState {
  readonly valid: boolean;
  /** Epoch ms at which the credential expires, if known. */
  readonly expiresAt?: number;
  /** True if the credential supports silent refresh (e.g. has a refresh token). */
  readonly refreshable: boolean;
}

export interface AuthorizeUrlRequest {
  readonly context: TenantContext;
  /** Where the provider should redirect after consent. */
  readonly redirectUri: string;
  /** Opaque anti-CSRF state the SDK generates and later verifies. */
  readonly state: string;
  /** Requested scopes. */
  readonly scopes: readonly string[];
}

export interface AuthorizeUrlResult {
  readonly url: string;
  /** PKCE verifier to be stored server-side and used at token exchange. */
  readonly codeVerifier?: string;
}

export interface ExchangeCodeRequest<TCredential> {
  readonly context: TenantContext;
  readonly code: string;
  readonly redirectUri: string;
  readonly codeVerifier?: string;
  /** The credential type is produced by exchange; present only for typing. */
  readonly _credential?: TCredential;
}

export interface RefreshRequest<TCredential> {
  readonly context: TenantContext;
  readonly current: TCredential;
}

/**
 * The authentication contract a connector must implement.
 */
export interface ConnectorAuthenticator<TCredential> {
  readonly method: AuthMethod;
  readonly defaultScopes: readonly string[];

  /**
   * Build the provider authorization URL. Only meaningful for interactive
   * flows (oauth2). Non-interactive methods may throw ConfigurationError.
   */
  buildAuthorizeUrl(request: AuthorizeUrlRequest): Promise<AuthorizeUrlResult>;

  /**
   * Exchange an authorization code (or equivalent) for a credential bundle.
   * The returned credential is handed to the Secret Management Layer for
   * KMS-enveloped persistence; the connector does not persist it directly.
   */
  exchangeCode(request: ExchangeCodeRequest<TCredential>): Promise<TCredential>;

  /**
   * Refresh an expiring/expired credential. MUST be idempotent-safe under
   * concurrent callers (the secret service serializes via row lock).
   */
  refresh(request: RefreshRequest<TCredential>): Promise<TCredential>;

  /**
   * Inspect a credential without calling the provider.
   */
  inspect(credential: TCredential): CredentialState;

  /**
   * Best-effort revocation at the provider. Local secret deletion happens
   * regardless of the outcome here.
   */
  revoke(context: TenantContext, credential: TCredential): Promise<void>;

  /**
   * Apply the credential to an outbound request (e.g. set Authorization
   * header). Returns the headers to merge. Throws ConnectorError on bad
   * credential shape.
   */
  authorizeRequest(credential: TCredential): Record<string, string>;
}

/** Convenience: the failure type all authenticator methods may throw. */
export type AuthError = ConnectorError;
