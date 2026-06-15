import {
  AuthenticationError,
  AuthorizationError,
  PermanentError,
  RateLimitError,
  TransientError,
  ValidationError,
  ConnectorErrorContext,
} from '../sdk/errors/ConnectorError';

/**
 * Minimal HTTP port + Zendesk-aware response mapping.
 *
 * We inject an `HttpClient` rather than calling fetch directly so the connector
 * is fully unit-testable and so retry/rate-limit behavior is observable. The
 * `mapResponseToError` function converts HTTP status + headers into the SDK
 * error taxonomy, which is what drives retry decisions upstream.
 *
 * Zendesk specifics handled here:
 *   - 429 with Retry-After (seconds) -> RateLimitError(retryAfterMs)
 *   - 401 -> AuthenticationError (triggers refresh-then-retry)
 *   - 403 -> AuthorizationError (scope/permission, non-retryable)
 *   - 422 -> ValidationError
 *   - 5xx -> TransientError (retryable)
 *   - other 4xx -> PermanentError
 */

export interface HttpRequest {
  readonly method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  readonly url: string;
  readonly headers?: Record<string, string>;
  readonly body?: string;
  readonly signal?: AbortSignal;
}

export interface HttpResponse {
  readonly status: number;
  readonly headers: Record<string, string>;
  readonly body: string;
}

export interface HttpClient {
  request(req: HttpRequest): Promise<HttpResponse>;
}

/** Parse Retry-After (seconds or HTTP-date) into ms. Defaults to 1s. */
export function parseRetryAfterMs(headerValue: string | undefined, nowMs: number): number {
  if (!headerValue) return 1000;
  const asInt = Number(headerValue);
  if (Number.isFinite(asInt)) return Math.max(0, asInt * 1000);
  const date = Date.parse(headerValue);
  if (!Number.isNaN(date)) return Math.max(0, date - nowMs);
  return 1000;
}

export function mapResponseToError(
  res: HttpResponse,
  context: ConnectorErrorContext,
  nowMs: number = Date.now(),
): Error | null {
  if (res.status >= 200 && res.status < 300) return null;

  const provider = { ...context, providerCode: String(res.status) };

  if (res.status === 429) {
    const retryAfterMs = parseRetryAfterMs(
      res.headers['retry-after'] ?? res.headers['Retry-After'],
      nowMs,
    );
    return new RateLimitError('Zendesk rate limit exceeded', retryAfterMs, provider);
  }
  if (res.status === 401) {
    return new AuthenticationError('Zendesk authentication failed', provider);
  }
  if (res.status === 403) {
    return new AuthorizationError('Zendesk authorization denied', provider);
  }
  if (res.status === 422) {
    return new ValidationError('Zendesk rejected request as invalid', provider);
  }
  if (res.status >= 500) {
    return new TransientError('Zendesk server error', provider, undefined, res.status);
  }
  return new PermanentError(`Zendesk request failed (${res.status})`, provider, undefined, res.status);
}
