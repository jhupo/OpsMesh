import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from hashlib import sha256

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.status import HTTP_429_TOO_MANY_REQUESTS
from starlette.types import ASGIApp

from backend.app.api.errors import error_response
from backend.app.core.config import Settings
from backend.app.core.metrics import record_http_request
from backend.app.core.request_context import request_id_var
from backend.app.core.trace_context import (
    PARENT_SPAN_ID_HEADER,
    SPAN_ID_HEADER,
    TRACE_ID_HEADER,
    TRACEPARENT_HEADER,
    TraceContext,
    reset_trace_context,
    set_trace_context,
    trace_context_from_headers,
    traceparent_header,
)
from backend.app.rate_limits.service import RedisFixedWindowRateLimiter

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Permitted-Cross-Domain-Policies": "none",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
    ) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request_id_token = request_id_var.set(request_id)
        trace_context = (
            trace_context_from_headers(request.headers)
            if self._settings.tracing_enabled
            else None
        )
        trace_tokens = set_trace_context(trace_context) if trace_context is not None else {}
        request.state.request_id = request_id
        if trace_context is not None:
            request.state.trace_id = trace_context.trace_id
            request.state.span_id = trace_context.span_id
            request.state.parent_span_id = trace_context.parent_span_id
        started_at = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            duration_ms = _elapsed_ms(started_at)
            response.headers["X-Process-Time-Ms"] = str(duration_ms)
            response.headers[REQUEST_ID_HEADER] = request_id
            if trace_context is not None:
                _set_trace_response_headers(response, trace_context)
            record_http_request(request.method, _metrics_path(request), status_code, duration_ms)
            log_level = (
                logging.WARNING
                if duration_ms >= self._settings.request_slow_log_threshold_ms
                else logging.INFO
            )
            logger.log(
                log_level,
                "HTTP request completed",
                extra=_request_log_extra(request, status_code, duration_ms),
            )
            return response
        except Exception:
            duration_ms = _elapsed_ms(started_at)
            logger.exception(
                "Unhandled request error",
                extra=_request_log_extra(request, status_code, duration_ms),
            )
            raise
        finally:
            reset_trace_context(trace_tokens)
            request_id_var.reset(request_id_token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for header_name, header_value in SECURITY_HEADERS.items():
            response.headers.setdefault(header_name, header_value)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limiter: RedisFixedWindowRateLimiter,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._limiter = limiter

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._settings.api_rate_limit_enabled:
            return await call_next(request)

        decision = self._limiter.check(
            identifier=self._identifier(request),
            limit=self._settings.api_rate_limit_requests,
            window_seconds=self._settings.api_rate_limit_window_seconds,
        )
        if not decision.allowed:
            response: Response = error_response(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                code="rate_limited",
                message="API rate limit exceeded",
                request_id=getattr(request.state, "request_id", None),
            )
        else:
            response = await call_next(request)

        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-RateLimit-Reset"] = str(decision.reset_epoch_seconds)
        return response

    @staticmethod
    def _identifier(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        user_id = request.headers.get("x-user-id")
        forwarded_for = request.headers.get("x-forwarded-for")
        client_host = forwarded_for.split(",", maxsplit=1)[0].strip() if forwarded_for else None
        if client_host is None and request.client is not None:
            client_host = request.client.host
        material = "|".join(
            [
                authorization,
                user_id or "",
                client_host or "unknown",
                request.url.path,
            ]
        )
        return sha256(material.encode("utf-8")).hexdigest()


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _metrics_path(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return route_path if isinstance(route_path, str) and route_path else request.url.path


def _request_log_extra(request: Request, status_code: int, duration_ms: int) -> dict[str, object]:
    client_host = request.client.host if request.client is not None else None
    return {
        "http_method": request.method,
        "http_path": request.url.path,
        "http_status": status_code,
        "duration_ms": duration_ms,
        "client_host": client_host,
    }


def _set_trace_response_headers(response: Response, context: TraceContext) -> None:
    response.headers[TRACE_ID_HEADER] = context.trace_id
    response.headers[SPAN_ID_HEADER] = context.span_id
    if context.parent_span_id is not None:
        response.headers[PARENT_SPAN_ID_HEADER] = context.parent_span_id
    response.headers[TRACEPARENT_HEADER] = traceparent_header(context)
