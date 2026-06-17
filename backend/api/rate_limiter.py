"""
rate_limiter.py
===============

Rate-limiting **abstraction** with a working in-memory implementation.

Why this file exists
--------------------
PMOS will be exposed to UIs and external integrations; without rate limiting a
single noisy client (or a runaway loop) can exhaust the RAG pipeline's
expensive resources. We need this from day one, but we also need the policy
and the backend store to be swappable (in-memory now, Redis later for
multi-instance deployments).

Design
------
* ``RateLimitPolicy``  — configurable limit + window, per scope.
* ``RateLimiter``      — abstract interface (``check(key) -> Decision``).
* ``InMemoryRateLimiter`` — token-bucket implementation, good for a single
  process and for tests. Thread-safe via a lock.
* ``RateLimitExceeded`` — raised/returned so middleware can emit a 429 and the
  metrics module can count ``rate_limit_events``.

Policies are keyed by *scope* (e.g. endpoint name or principal). The default
policy applies when no specific scope policy is configured.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


class RateLimitExceeded(Exception):
    """Raised when a caller exceeds its allotted rate. Mapped to 429."""

    def __init__(self, retry_after: float, limit: int, window: float) -> None:
        super().__init__("Rate limit exceeded")
        self.retry_after = retry_after
        self.limit = limit
        self.window = window


@dataclass(frozen=True)
class RateLimitPolicy:
    """How many requests are allowed per rolling window of ``window`` seconds."""

    limit: int = 60
    window: float = 60.0  # seconds


@dataclass
class Decision:
    allowed: bool
    remaining: int
    limit: int
    window: float
    retry_after: float = 0.0


class RateLimiter:
    """Abstract rate limiter. Concrete backends implement ``check``."""

    def check(self, key: str, scope: str = "default") -> Decision:
        raise NotImplementedError


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class InMemoryRateLimiter(RateLimiter):
    """
    Token-bucket limiter held in process memory.

    Each ``(scope, key)`` pair owns a bucket that refills continuously at
    ``limit / window`` tokens per second up to a ceiling of ``limit``. A
    request consumes one token. When no tokens remain the request is denied
    and a ``retry_after`` hint is computed.

    Swap this for a Redis-backed limiter to share state across instances; the
    ``RateLimiter`` interface stays identical.
    """

    def __init__(
        self,
        default_policy: Optional[RateLimitPolicy] = None,
        scope_policies: Optional[Dict[str, RateLimitPolicy]] = None,
    ) -> None:
        self._default = default_policy or RateLimitPolicy()
        self._scopes = scope_policies or {}
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _policy_for(self, scope: str) -> RateLimitPolicy:
        return self._scopes.get(scope, self._default)

    def check(self, key: str, scope: str = "default") -> Decision:
        policy = self._policy_for(scope)
        rate = policy.limit / policy.window
        now = time.monotonic()
        bucket_key = f"{scope}:{key}"

        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                bucket = _Bucket(tokens=float(policy.limit), last_refill=now)
                self._buckets[bucket_key] = bucket

            # Refill based on elapsed time.
            elapsed = now - bucket.last_refill
            bucket.tokens = min(policy.limit, bucket.tokens + elapsed * rate)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return Decision(
                    allowed=True,
                    remaining=int(bucket.tokens),
                    limit=policy.limit,
                    window=policy.window,
                )

            # Not enough tokens: compute time until one token is available.
            deficit = 1.0 - bucket.tokens
            retry_after = deficit / rate if rate > 0 else policy.window
            return Decision(
                allowed=False,
                remaining=0,
                limit=policy.limit,
                window=policy.window,
                retry_after=round(retry_after, 3),
            )


# Active limiter for this slice. Generous default; per-endpoint scopes tighten
# the expensive RAG routes.
_ACTIVE_LIMITER: RateLimiter = InMemoryRateLimiter(
    default_policy=RateLimitPolicy(limit=120, window=60.0),
    scope_policies={
        "answer": RateLimitPolicy(limit=30, window=60.0),
        "search": RateLimitPolicy(limit=60, window=60.0),
        "documents.ingest": RateLimitPolicy(limit=20, window=60.0),
        "documents.process": RateLimitPolicy(limit=20, window=60.0),
    },
)


def set_rate_limiter(limiter: RateLimiter) -> None:
    global _ACTIVE_LIMITER
    _ACTIVE_LIMITER = limiter


def get_rate_limiter() -> RateLimiter:
    return _ACTIVE_LIMITER
