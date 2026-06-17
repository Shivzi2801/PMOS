"""Answer generation pipeline.

Orchestrates the end-to-end flow:

    PromptPackage
        -> safety preflight (empty / low-score context gates)
        -> prompt rendering
        -> provider invocation (with bounded retry on transient errors)
        -> citation binding
        -> grounding enforcement
        -> safety evaluation (status + confidence threshold)
        -> GeneratedAnswer

All side-effects (metrics, sleeps) are injected, keeping the pipeline pure and
fully unit-testable with the mock provider.
"""

from __future__ import annotations

from typing import Optional

from ..citation.binder import bind_citations
from ..contracts.errors import ProviderError
from ..contracts.models import (
    AnswerStatus,
    GeneratedAnswer,
    LLMResponse,
    PromptPackage,
    TokenUsage,
    now_ms,
)
from ..grounding.enforcer import enforce
from ..observability.metrics import (
    M_COMPLETION_TOKENS,
    M_FAILURES,
    M_LATENCY_MS,
    M_PROMPT_TOKENS,
    M_REQUESTS,
    M_RETRIES,
    M_TOTAL_TOKENS,
    MetricsSink,
    NullMetricsSink,
)
from ..providers.base import LLMProvider
from ..safety import controls
from .prompt_builder import build_request
from .retry import RetryPolicy, SleepFn, default_sleep


class GenerationPipeline:
    """Coordinates providers, grounding, citation and safety layers."""

    def __init__(
        self,
        provider: LLMProvider,
        retry_policy: Optional[RetryPolicy] = None,
        metrics: Optional[MetricsSink] = None,
        sleep: SleepFn = default_sleep,
    ) -> None:
        self._provider = provider
        self._retry = retry_policy or RetryPolicy()
        self._metrics = metrics or NullMetricsSink()
        self._sleep = sleep

    # -- public API ---------------------------------------------------------- #

    def generate(self, package: PromptPackage) -> GeneratedAnswer:
        started = now_ms()
        self._metrics.increment(M_REQUESTS, provider=self._provider.name)

        # 1) Pre-flight safety gates (no provider call on failure).
        pre = controls.preflight(package)
        if not pre.allowed:
            return self._terminal(
                package, pre.status or AnswerStatus.EMPTY_CONTEXT, pre.reason, started
            )

        # 2) Render + 3) invoke provider with retry.
        request = build_request(package)
        try:
            response = self._invoke_with_retry(request)
        except ProviderError as exc:
            return self._terminal(
                package,
                AnswerStatus.PROVIDER_ERROR,
                f"{type(exc).__name__}: {exc}",
                started,
            )

        self._record_usage(response.usage)

        # 4) Bind citations.
        binding = bind_citations(response.text, package)

        # 5) Enforce grounding + compute confidence.
        verdict = enforce(package, binding, response.text)

        # 6) Evaluate terminal status against thresholds.
        status = controls.evaluate(package, verdict)

        latency = now_ms() - started
        self._metrics.observe(
            M_LATENCY_MS, latency, provider=self._provider.name, status=status.value
        )
        if status not in (AnswerStatus.OK,):
            self._metrics.increment(
                M_FAILURES, provider=self._provider.name, kind=status.value
            )

        return GeneratedAnswer(
            request_id=package.request_id,
            status=status,
            text=response.text,
            confidence=verdict.confidence,
            bound_citations=binding.bound,
            supporting_chunk_ids=binding.supporting_chunk_ids,
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            latency_ms=latency,
            diagnostics={
                "grounding_reason": verdict.reason,
                "unknown_markers": ",".join(binding.unknown_markers),
            },
        )

    # -- internals ----------------------------------------------------------- #

    def _invoke_with_retry(self, request) -> LLMResponse:
        attempt = 0
        last_exc: Optional[ProviderError] = None
        while attempt < self._retry.max_attempts:
            attempt += 1
            try:
                return self._provider.generate(request)
            except ProviderError as exc:
                last_exc = exc
                if not RetryPolicy.is_retryable(exc):
                    raise
                if attempt >= self._retry.max_attempts:
                    raise
                self._metrics.increment(
                    M_RETRIES, provider=self._provider.name, attempt=str(attempt)
                )
                self._sleep(self._retry.delay_for(attempt))
        assert last_exc is not None  # pragma: no cover
        raise last_exc

    def _record_usage(self, usage: TokenUsage) -> None:
        p = self._provider.name
        self._metrics.observe(M_PROMPT_TOKENS, usage.prompt_tokens, provider=p)
        self._metrics.observe(M_COMPLETION_TOKENS, usage.completion_tokens, provider=p)
        self._metrics.observe(M_TOTAL_TOKENS, usage.total_tokens, provider=p)

    def _terminal(
        self,
        package: PromptPackage,
        status: AnswerStatus,
        reason: str,
        started: float,
    ) -> GeneratedAnswer:
        latency = now_ms() - started
        self._metrics.observe(
            M_LATENCY_MS, latency, provider=self._provider.name, status=status.value
        )
        self._metrics.increment(
            M_FAILURES, provider=self._provider.name, kind=status.value
        )
        text = (
            "I don't have enough information in the provided context to answer."
            if status
            in (
                AnswerStatus.EMPTY_CONTEXT,
                AnswerStatus.LOW_CONFIDENCE,
                AnswerStatus.UNSUPPORTED,
            )
            else ""
        )
        return GeneratedAnswer(
            request_id=package.request_id,
            status=status,
            text=text,
            confidence=0.0,
            provider=self._provider.name,
            latency_ms=latency,
            diagnostics={"reason": reason},
        )
