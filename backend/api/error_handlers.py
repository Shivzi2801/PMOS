"""
error_handlers.py
=================

Centralized translation of exceptions into the standard error envelope.

Why this file exists
--------------------
The spec requires consistent handling of: validation failures, missing
resources, pipeline failures, timeouts, rate-limit violations, authentication
failures, and unexpected exceptions. Rather than try/except in every route,
each failure type raises a typed exception (or FastAPI raises its own), and a
handler registered here converts it to a ``ResponseEnvelope`` error with the
correct HTTP status and a stable machine-readable ``code``.

Benefits:
* one place defines the error contract,
* clients always get the same JSON shape on failure,
* nothing leaks stack traces or internal detail to callers.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth_stub import AuthError
from .rate_limiter import RateLimitExceeded
from .response_models import ErrorDetail, ResponseEnvelope

logger = logging.getLogger("pmos.api.errors")


# --------------------------------------------------------------------------- #
# Domain exceptions raised by route handlers / service adapters.
# --------------------------------------------------------------------------- #
class ResourceNotFoundError(Exception):
    """A requested resource (connector, document, job) does not exist."""

    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(f"{resource} '{identifier}' not found")
        self.resource = resource
        self.identifier = identifier


class PipelineError(Exception):
    """A downstream service module failed while processing the request."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage} failed: {message}")
        self.stage = stage
        self.detail = message


class PipelineTimeoutError(Exception):
    """A downstream stage exceeded its time budget."""

    def __init__(self, stage: str, timeout_s: float) -> None:
        super().__init__(f"{stage} timed out after {timeout_s}s")
        self.stage = stage
        self.timeout_s = timeout_s


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _envelope(request: Request, errors, status_code: int) -> JSONResponse:
    payload = ResponseEnvelope.fail(_request_id(request), errors)
    return JSONResponse(status_code=status_code, content=payload.model_dump())


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_error_handlers(app: FastAPI) -> None:
    """Attach every handler to the application."""

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            errors.append(
                ErrorDetail(
                    code="validation_error",
                    message=err.get("msg", "Invalid value"),
                    field=loc or None,
                )
            )
        return _envelope(request, errors, status_code=422)

    @app.exception_handler(AuthError)
    async def _auth(request: Request, exc: AuthError):
        return _envelope(
            request,
            [ErrorDetail(code="authentication_failed", message=exc.message)],
            status_code=401,
        )

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit(request: Request, exc: RateLimitExceeded):
        resp = _envelope(
            request,
            [ErrorDetail(
                code="rate_limit_exceeded",
                message=f"Rate limit of {exc.limit} per {exc.window}s exceeded",
            )],
            status_code=429,
        )
        resp.headers["Retry-After"] = str(int(exc.retry_after) + 1)
        return resp

    @app.exception_handler(ResourceNotFoundError)
    async def _not_found(request: Request, exc: ResourceNotFoundError):
        return _envelope(
            request,
            [ErrorDetail(
                code="resource_not_found",
                message=str(exc),
                field=exc.resource,
            )],
            status_code=404,
        )

    @app.exception_handler(PipelineTimeoutError)
    async def _timeout(request: Request, exc: PipelineTimeoutError):
        return _envelope(
            request,
            [ErrorDetail(
                code="pipeline_timeout",
                message=str(exc),
                field=exc.stage,
            )],
            status_code=504,
        )

    @app.exception_handler(PipelineError)
    async def _pipeline(request: Request, exc: PipelineError):
        logger.error("Pipeline failure in stage %s: %s", exc.stage, exc.detail)
        return _envelope(
            request,
            [ErrorDetail(
                code="pipeline_error",
                message=str(exc),
                field=exc.stage,
            )],
            status_code=502,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return _envelope(
            request,
            [ErrorDetail(code="http_error", message=str(exc.detail))],
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        # Never leak internals; log full detail server-side only.
        logger.exception("Unhandled exception: %s", exc)
        return _envelope(
            request,
            [ErrorDetail(
                code="internal_error",
                message="An unexpected error occurred.",
            )],
            status_code=500,
        )
