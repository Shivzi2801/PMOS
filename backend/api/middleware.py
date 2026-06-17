"""
middleware.py
=============

Cross-cutting request processing implemented as ASGI/Starlette middleware.

Why this file exists
--------------------
Several concerns must wrap *every* request regardless of which endpoint it
hits: assigning a correlation/request id, timing the request, structured
logging, recording metrics, and enforcing rate limits. Implementing these once
as middleware keeps route handlers focused purely on business logic.

Order of operations (outermost first)
-------------------------------------
1. ``CorrelationIdMiddleware`` — assign/propagate ``request_id`` and expose it
   on ``request.state`` and the ``X-Request-ID`` response header.
2. ``RateLimitMiddleware``     — enforce the configured policy; on breach emit
   429 via the rate-limit exception path and count the event.
3. ``TimingMetricsMiddleware`` — measure latency, log the outcome, and record
   request/error/latency metrics.

Because ``request_id`` is set first, every downstream log line and every error
envelope can reference the same id, giving end-to-end traceability.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .api_metrics import get_metrics_registry
from .rate_limiter import RateLimitExceeded, get_rate_limiter
from .response_models import ErrorDetail, ResponseEnvelope

logger = logging.getLogger("pmos.api.access")

REQUEST_ID_HEADER = "X-Request-ID"


def _route_scope(path: str) -> str:
    """Derive a rate-limit scope + metrics label from the path."""
    # Normalize "/api/v1/documents/ingest" -> "documents.ingest", etc.
    parts = [p for p in path.split("/") if p and p not in ("api",)]
    if parts and parts[0].startswith("v"):
        parts = parts[1:]
    return ".".join(parts) if parts else "root"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assign a correlation id to every request and echo it back."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce rate limits before the request reaches a handler."""

    async def dispatch(self, request: Request, call_next):
        scope = _route_scope(request.url.path)

        # Health/version/metrics are operational and not rate limited.
        if scope in ("health", "version", "metrics", "root", "docs", "openapi.json"):
            return await call_next(request)

        client = request.client.host if request.client else "anonymous"
        limiter = get_rate_limiter()
        decision = limiter.check(key=client, scope=scope)

        if not decision.allowed:
            get_metrics_registry().record_rate_limit_event()
            request_id = getattr(request.state, "request_id", "unknown")
            payload = ResponseEnvelope.fail(
                request_id,
                [ErrorDetail(
                    code="rate_limit_exceeded",
                    message=(
                        f"Rate limit of {decision.limit} per "
                        f"{decision.window}s exceeded"
                    ),
                )],
            )
            resp = JSONResponse(status_code=429, content=payload.model_dump())
            resp.headers[REQUEST_ID_HEADER] = request_id
            resp.headers["Retry-After"] = str(int(decision.retry_after) + 1)
            return resp

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response


class TimingMetricsMiddleware(BaseHTTPMiddleware):
    """Time each request, log the outcome, and record metrics."""

    async def dispatch(self, request: Request, call_next):
        scope = _route_scope(request.url.path)
        start = time.perf_counter()
        request_id = getattr(request.state, "request_id", "unknown")

        try:
            response: Response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            get_metrics_registry().record_request(scope, elapsed_ms, is_error=True)
            logger.exception(
                "request_id=%s method=%s path=%s status=500 latency_ms=%.2f",
                request_id, request.method, request.url.path, elapsed_ms,
            )
            raise  # let the registered exception handlers build the envelope

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        is_error = response.status_code >= 400
        get_metrics_registry().record_request(scope, elapsed_ms, is_error)
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.2f}"
        logger.info(
            "request_id=%s method=%s path=%s status=%d latency_ms=%.2f",
            request_id, request.method, request.url.path,
            response.status_code, elapsed_ms,
        )
        return response


def register_middleware(app) -> None:
    """
    Attach middleware. Starlette runs the LAST-added middleware OUTERMOST,
    so we add in reverse of the desired execution order to get:
        CorrelationId -> RateLimit -> TimingMetrics -> route
    """
    app.add_middleware(TimingMetricsMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
