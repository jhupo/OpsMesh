"""HTTP transport mechanisms with gateway policies supplied by the application."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.status import HTTP_429_TOO_MANY_REQUESTS, HTTP_503_SERVICE_UNAVAILABLE
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from opsmesh.shared.http.errors import error_response
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter

logger = logging.getLogger(__name__)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Permitted-Cross-Domain-Policies": "none",
}


@dataclass(frozen=True)
class GatewayRateLimitPolicy:
    name: str
    limit: int
    fail_closed: bool


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_headers)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: FixedWindowRateLimiter | Callable[[Request], FixedWindowRateLimiter],
        policy_for_path: Callable[[str], GatewayRateLimitPolicy],
        identifier: Callable[[Request, GatewayRateLimitPolicy], str],
        enabled: bool,
        window_seconds: int,
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._policy_for_path = policy_for_path
        self._identifier = identifier
        self._enabled = enabled
        self._window_seconds = window_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._enabled:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        policy = self._policy_for_path(request.url.path)
        limiter = self._limiter(request) if callable(self._limiter) else self._limiter
        decision = await run_in_threadpool(
            limiter.check,
            identifier=self._identifier(request, policy),
            limit=policy.limit,
            window_seconds=self._window_seconds,
        )

        async def send_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-RateLimit-Limit"] = str(decision.limit)
                headers["X-RateLimit-Remaining"] = str(decision.remaining)
                headers["X-RateLimit-Reset"] = str(decision.reset_epoch_seconds)
                if message["status"] == HTTP_429_TOO_MANY_REQUESTS:
                    headers["Retry-After"] = str(
                        max(decision.reset_epoch_seconds - int(time.time()), 1)
                    )
            await send(message)

        if not decision.backend_available and policy.fail_closed:
            status, code, message = (
                HTTP_503_SERVICE_UNAVAILABLE,
                "rate_limit_unavailable",
                "Request gateway is temporarily unavailable",
            )
        elif not decision.allowed:
            status, code, message = (
                HTTP_429_TOO_MANY_REQUESTS,
                "rate_limited",
                "API rate limit exceeded",
            )
        else:
            await self.app(scope, receive, send_headers)
            return
        logger.log(
            logging.ERROR if status == HTTP_503_SERVICE_UNAVAILABLE else logging.WARNING,
            "Gateway request rejected",
            extra={
                "rate_limit_policy": policy.name,
                "http_path": request.url.path,
                "request_id": getattr(request.state, "request_id", None),
                "error_code": code,
            },
        )
        response = error_response(
            status_code=status,
            code=code,
            message=message,
            request_id=getattr(request.state, "request_id", None),
        )
        await response(scope, receive, send_headers)
