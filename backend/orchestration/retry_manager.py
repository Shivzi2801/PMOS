"""
Retry manager.

Wraps an arbitrary callable and retries it according to a configurable policy:
exponential backoff with jitter, a maximum attempt count, and failure
classification (only RETRYABLE failures are retried; TERMINAL failures abort
immediately). Designed to wrap both whole workflows and individual steps.

The manager is sleep-injectable so tests run instantly without real waits.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from time import sleep as _real_sleep
from typing import Callable, Optional, TypeVar

from .errors import FailureClass, RetryExhaustedError, classify

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_attempts: int = 3              # total tries, including the first
    base_delay_s: float = 0.5         # initial backoff
    max_delay_s: float = 30.0         # ceiling on any single backoff
    multiplier: float = 2.0           # exponential growth factor
    jitter: float = 0.1               # +/- fraction randomisation to avoid thundering herd

    def delay_for(self, attempt: int) -> float:
        """Backoff before `attempt` (1-indexed). attempt=1 => no prior delay."""
        raw = self.base_delay_s * (self.multiplier ** (attempt - 1))
        raw = min(raw, self.max_delay_s)
        if self.jitter:
            spread = raw * self.jitter
            raw = raw + random.uniform(-spread, spread)
        return max(0.0, raw)


class RetryManager:
    def __init__(self, policy: Optional[RetryPolicy] = None, *, sleep=None,
                 on_retry: Optional[Callable[[int, BaseException, float], None]] = None):
        self.policy = policy or RetryPolicy()
        self._sleep = sleep or _real_sleep
        self._on_retry = on_retry

    def run(self, fn: Callable[[], T], *, is_cancelled: Callable[[], bool] = None) -> T:
        """
        Execute `fn`, retrying on retryable failures.

        `is_cancelled` is polled before each attempt so a cancelled job stops
        retrying promptly.
        """
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.policy.max_attempts + 1):
            if is_cancelled and is_cancelled():
                raise RetryExhaustedError(
                    "cancelled before attempt", attempts=attempt - 1,
                    last_error=last_exc,
                )
            if attempt > 1:
                delay = self.policy.delay_for(attempt)
                if self._on_retry:
                    self._on_retry(attempt, last_exc, delay)
                self._sleep(delay)
            try:
                return fn()
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if classify(exc) is not FailureClass.RETRYABLE:
                    # Terminal failure: do not retry, propagate as-is.
                    raise
                # else loop and retry
        raise RetryExhaustedError(
            f"exhausted {self.policy.max_attempts} attempts",
            attempts=self.policy.max_attempts,
            last_error=last_exc,
        )
