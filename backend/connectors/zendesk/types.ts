/**
 * Zendesk credential + configuration types.
 *
 * The OAuth credential bundle is the connector's private `TCredential`. It is
 * persisted only via the Secret Management Layer (KMS-enveloped) and never
 * logged. `expiresAt` is epoch ms; Zendesk OAuth access tokens for the API are
 * long-lived but we still model refresh for providers/configs that issue
 * refresh tokens, and to keep the contract uniform across connectors.
 */

export interface ZendeskOAuthCredential {
  readonly accessToken: string;
  readonly refreshToken?: string;
  readonly tokenType: string; // typically "bearer"
  readonly scope: string;
  /** Epoch ms expiry, if the token is expiring. */
  readonly expiresAt?: number;
}

export interface ZendeskConfig {
  /** Zendesk subdomain, e.g. "acme" for acme.zendesk.com. */
  readonly subdomain: string;
}

export function zendeskBaseUrl(config: ZendeskConfig): string {
  return `https://${config.subdomain}.zendesk.com`;
}

export function zendeskOAuthBaseUrl(config: ZendeskConfig): string {
  return `https://${config.subdomain}.zendesk.com`;
}

const SUBDOMAIN_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i;

export function isValidSubdomain(subdomain: string): boolean {
  return SUBDOMAIN_RE.test(subdomain);
}
