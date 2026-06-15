import { HttpClient, HttpRequest, HttpResponse } from './http';

/**
 * FetchHttpClient
 *
 * Production HttpClient backed by the global `fetch` (Node 18+/undici). Kept
 * deliberately thin: no retry, no rate-limit logic — those live in the SDK
 * retry engine and the http error mapper so behavior is uniform across
 * connectors and testable without a network.
 *
 * A request timeout is enforced via AbortController, composed with any
 * caller-supplied signal.
 */

export interface FetchHttpClientOptions {
  readonly timeoutMs?: number;
  /** Override for tests; defaults to global fetch. */
  readonly fetchImpl?: typeof fetch;
}

export class FetchHttpClient implements HttpClient {
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: FetchHttpClientOptions = {}) {
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async request(req: HttpRequest): Promise<HttpResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    // Compose caller signal with our timeout signal.
    const onAbort = () => controller.abort();
    if (req.signal) {
      if (req.signal.aborted) controller.abort();
      else req.signal.addEventListener('abort', onAbort, { once: true });
    }

    try {
      const res = await this.fetchImpl(req.url, {
        method: req.method,
        headers: req.headers,
        body: req.body,
        signal: controller.signal,
      });
      const body = await res.text();
      const headers: Record<string, string> = {};
      res.headers.forEach((value, key) => {
        headers[key.toLowerCase()] = value;
      });
      return { status: res.status, headers, body };
    } finally {
      clearTimeout(timeout);
      req.signal?.removeEventListener('abort', onAbort);
    }
  }
}
