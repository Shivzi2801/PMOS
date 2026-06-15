import { describe, it, expect, vi } from 'vitest';
import { computeDelay, executeWithRetry, DEFAULT_BACKOFF } from '../../sdk/retry/retry';
import {
  RateLimitError,
  TransientError,
  PermanentError,
  ConnectorErrorCategory,
} from '../../sdk/errors/ConnectorError';

describe('retry/computeDelay', () => {
  it('honors explicit retryAfterMs over exponential, capped at maxDelay', () => {
    const err = new RateLimitError('slow down', 999_999);
    const delay = computeDelay(1, DEFAULT_BACKOFF, err, () => 0.5);
    expect(delay).toBe(DEFAULT_BACKOFF.maxDelayMs);
  });

  it('grows exponentially with attempt number', () => {
    const err = new TransientError('boom');
    const noJitter = { ...DEFAULT_BACKOFF, jitter: 0 };
    const d1 = computeDelay(1, noJitter, err, () => 0);
    const d2 = computeDelay(2, noJitter, err, () => 0);
    const d3 = computeDelay(3, noJitter, err, () => 0);
    expect(d1).toBe(250);
    expect(d2).toBe(500);
    expect(d3).toBe(1000);
  });

  it('applies jitter within the configured band', () => {
    const err = new TransientError('boom');
    const policy = { ...DEFAULT_BACKOFF, jitter: 0.2 };
    const low = computeDelay(1, policy, err, () => 0); // factor 0.8
    const high = computeDelay(1, policy, err, () => 0.999999); // factor ~1.2
    expect(low).toBe(200);
    expect(high).toBeGreaterThanOrEqual(299);
    expect(high).toBeLessThanOrEqual(300);
  });
});

describe('retry/executeWithRetry', () => {
  const instantSleep = async () => {};

  it('returns immediately on first success', async () => {
    const fn = vi.fn(async () => 'ok');
    const res = await executeWithRetry(fn, { sleep: instantSleep });
    expect(res.value).toBe('ok');
    expect(res.attempts).toBe(1);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('retries transient errors up to maxAttempts then throws', async () => {
    const fn = vi.fn(async () => {
      throw new TransientError('always fails');
    });
    await expect(
      executeWithRetry(fn, { sleep: instantSleep, policy: { maxAttempts: 3 } }),
    ).rejects.toMatchObject({ category: ConnectorErrorCategory.Transient });
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('does not retry permanent errors', async () => {
    const fn = vi.fn(async () => {
      throw new PermanentError('nope');
    });
    await expect(executeWithRetry(fn, { sleep: instantSleep })).rejects.toMatchObject({
      category: ConnectorErrorCategory.Permanent,
    });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('eventually succeeds after transient failures', async () => {
    let n = 0;
    const fn = vi.fn(async () => {
      if (++n < 3) throw new TransientError('flaky');
      return 'recovered';
    });
    const res = await executeWithRetry(fn, { sleep: instantSleep, random: () => 0.5 });
    expect(res.value).toBe('recovered');
    expect(res.attempts).toBe(3);
  });

  it('invokes onRetry with attempt + delay', async () => {
    const onRetry = vi.fn();
    let n = 0;
    const fn = async () => {
      if (++n < 2) throw new TransientError('once');
      return 1;
    };
    await executeWithRetry(fn, { sleep: instantSleep, onRetry, random: () => 0 });
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onRetry.mock.calls[0][0]).toMatchObject({ attempt: 1 });
  });
});
