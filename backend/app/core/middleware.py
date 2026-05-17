import logging
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
from backend.app.core.request_context import request_id_var
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
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error")
            raise
        finally:
            request_id_var.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


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
