import {
  ConnectorError,
  ConnectorErrorCategory,
  toConnectorError,
} from '../errors/ConnectorError';

/**
 * Retry contracts.
 *
 * The retry engine is deliberately pure and side-effect-free except for the
 * injected `sleep` and `now` functions, which makes it fully unit-testable
 * with virtual time. Connectors never implement their own retry loops; they
 * declare a policy and hand work to `executeWithRetry`.
 */

export interface BackoffPolicy {
  /** Maximum number of attempts INCLUDING the first. Must be >= 1. */
  readonly maxAttempts: number;
  /** Base delay in ms for exponential backoff. */
  readonly baseDelayMs: number;
  /** Hard ceiling on any single delay, in ms. */
  readonly maxDelayMs: number;
  /**
   * Jitter factor in [0, 1]. The actual delay is multiplied by a random value
   * in [1 - jitter, 1 + jitter] to avoid thundering herds.
   */
  readonly jitter: number;
  /** Multiplier applied per attempt. 2 == classic exponential. */
  readonly multiplier: number;
}

export const DEFAULT_BACKOFF: BackoffPolicy = {
  maxAttempts: 5,
  baseDelayMs: 250,
  maxDelayMs: 30_000,
  jitter: 0.2,
  multiplier: 2,
};

export interface RetryAttemptInfo {
  /** 1-based attempt number that just failed. */
  readonly attempt: number;
  readonly error: ConnectorError;
  /** Computed delay before the next attempt, in ms. */
  readonly delayMs: number;
}

export interface RetryOptions {
  readonly policy?: Partial<BackoffPolicy>;
  /** Observability hook fired before each backoff sleep. */
  readonly onRetry?: (info: RetryAttemptInfo) => void;
  /** Injectable for tests. Defaults to real timers. */
  readonly sleep?: (ms: number) => Promise<void>;
  /** Injectable for tests / deterministic jitter. Defaults to Math.random. */
  readonly random?: () => number;
  /**
   * Categories that may be retried. Defaults to RateLimited + Transient +
   * Authentication + Unknown. Authentication is included because the auth layer
   * wraps refresh-then-retry behind this engine.
   */
  readonly retryableCategories?: ReadonlySet<ConnectorErrorCategory>;
  /** AbortSignal to cancel between attempts. */
  readonly signal?: AbortSignal;
}

const DEFAULT_RETRYABLE: ReadonlySet<ConnectorErrorCategory> = new Set([
  ConnectorErrorCategory.RateLimited,
  ConnectorErrorCategory.Transient,
  ConnectorErrorCategory.Authentication,
  ConnectorErrorCategory.Unknown,
]);

const realSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Computes the delay for a given (1-based) attempt under a policy.
 * Honors an explicit `retryAfterMs` from the error (e.g. Retry-After header),
 * which always takes precedence over the computed exponential delay.
 */
export function computeDelay(
  attempt: number,
  policy: BackoffPolicy,
  error: ConnectorError,
  random: () => number,
): number {
  if (typeof error.retryAfterMs === 'number' && error.retryAfterMs >= 0) {
    return Math.min(error.retryAfterMs, policy.maxDelayMs);
  }
  const exponential = policy.baseDelayMs * Math.pow(policy.multiplier, attempt - 1);
  const capped = Math.min(exponential, policy.maxDelayMs);
  const jitterRange = capped * policy.jitter;
  // random() in [0,1) -> factor in [1 - jitter, 1 + jitter)
  const factor = 1 - policy.jitter + random() * (2 * policy.jitter);
  const jittered = capped * factor;
  void jitterRange;
  return Math.max(0, Math.round(jittered));
}

export interface RetryResult<T> {
  readonly value: T;
  readonly attempts: number;
}

/**
 * Executes `fn` with retry/backoff. `fn` receives the 1-based attempt number.
 * Throws the final ConnectorError if all attempts are exhausted or the error
 * is non-retryable.
 */
export async function executeWithRetry<T>(
  fn: (attempt: number) => Promise<T>,
  options: RetryOptions = {},
): Promise<RetryResult<T>> {
  const policy: BackoffPolicy = { ...DEFAULT_BACKOFF, ...(options.policy ?? {}) };
  if (policy.maxAttempts < 1) {
    throw new Error('BackoffPolicy.maxAttempts must be >= 1');
  }
  const sleep = options.sleep ?? realSleep;
  const random = options.random ?? Math.random;
  const retryable = options.retryableCategories ?? DEFAULT_RETRYABLE;

  let lastError: ConnectorError | undefined;

  for (let attempt = 1; attempt <= policy.maxAttempts; attempt++) {
    if (options.signal?.aborted) {
      throw toConnectorError(new Error('Operation aborted'), { operation: 'retry' });
    }
    try {
      const value = await fn(attempt);
      return { value, attempts: attempt };
    } catch (raw) {
      const error = toConnectorError(raw);
      lastError = error;

      const isLastAttempt = attempt >= policy.maxAttempts;
      const categoryRetryable = error.retryable && retryable.has(error.category);

      if (isLastAttempt || !categoryRetryable) {
        throw error;
      }

      const delayMs = computeDelay(attempt, policy, error, random);
      options.onRetry?.({ attempt, error, delayMs });
      await sleep(delayMs);
    }
  }

  // Unreachable in practice, but satisfies the type checker.
  throw lastError ?? toConnectorError(new Error('Retry loop exited unexpectedly'));
}
