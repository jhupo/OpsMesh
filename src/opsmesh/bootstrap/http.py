"""HTTP request policies, correlation, request logs and API metrics."""

import logging
import re
import time
import uuid
from collections.abc import Callable
from hashlib import sha256

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from opsmesh.config import Settings
from opsmesh.shared.http.client_ip import resolve_client_ip
from opsmesh.shared.http.middleware import GatewayRateLimitPolicy
from opsmesh.telemetry.context import bind_log_context, capture_trace_context
from opsmesh.telemetry.metrics import increment_counter, observe_histogram
from opsmesh.telemetry.tracing import current_trace_context, parent_span_id

logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
HTTP_DURATION_BUCKETS = (10, 50, 100, 250, 500, 1000, 2500, 5000)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, slow_log_threshold_ms: int) -> None:
        self.app = app
        self._slow_log_threshold_ms = slow_log_threshold_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else uuid.uuid4().hex
        )
        with bind_log_context(replace=True, request_id=request_id):
            metadata = current_trace_context()
            parent_id = parent_span_id(dict(request.headers))
            request.state.request_id = request_id
            request.state.trace_id = metadata.get("trace_id")
            request.state.span_id = metadata.get("span_id")
            request.state.parent_span_id = parent_id
            started_at = time.perf_counter()
            status_code = 500

            async def send_headers(message: Message) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                    headers = MutableHeaders(scope=message)
                    headers["X-Process-Time-Ms"] = str(_elapsed_ms(started_at))
                    headers["X-Request-ID"] = request_id
                    if metadata:
                        headers["X-Trace-ID"] = metadata["trace_id"]
                        headers["X-Span-ID"] = metadata["span_id"]
                        if parent_id is not None:
                            headers["X-Parent-Span-ID"] = parent_id
                        carrier = capture_trace_context()
                        if "traceparent" in carrier:
                            headers["traceparent"] = carrier["traceparent"]
                await send(message)

            try:
                await self.app(scope, receive, send_headers)
            except Exception:
                logger.exception(
                    "Unhandled request error",
                    extra=_request_log_extra(request, status_code, _elapsed_ms(started_at)),
                )
                raise
            finally:
                duration_ms = _elapsed_ms(started_at)
                record_http_request(
                    request.method, _metrics_path(request), status_code, duration_ms
                )
                logger.log(
                    logging.WARNING if duration_ms >= self._slow_log_threshold_ms else logging.INFO,
                    "HTTP request completed",
                    extra=_request_log_extra(request, status_code, duration_ms),
                )


def record_http_request(method: str, path: str, status_code: int, duration_ms: int) -> None:
    labels = {
        "method": method if method in HTTP_METHODS else "OTHER",
        "path": path or "/",
        "status": str(status_code),
    }
    increment_counter(
        "opsmesh_http_requests_total",
        labels=labels,
        help_text="Total HTTP requests served by the API.",
    )
    observe_histogram(
        "opsmesh_http_request_duration_ms",
        float(duration_ms),
        buckets=HTTP_DURATION_BUCKETS,
        labels=labels,
        help_text="HTTP request duration in milliseconds.",
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _metrics_path(request: Request) -> str:
    route_path = getattr(request.scope.get("route"), "path", None)
    return route_path if isinstance(route_path, str) and route_path else "/__unmatched__"


def _request_log_extra(request: Request, status_code: int, duration_ms: int) -> dict[str, object]:
    attributes: dict[str, object] = {
        "http_method": request.method,
        "http_path": request.url.path,
        "http_status": status_code,
        "duration_ms": duration_ms,
        "client_host": request.client.host if request.client is not None else None,
    }
    attributes.update(getattr(request.state, "log_context", {}))
    return attributes


AUTHENTICATION_PATH_SEGMENT = "auth"
PLATFORM_ADMIN_PATH_SEGMENT = "platform"


def gateway_policy(settings: Settings) -> Callable[[str], GatewayRateLimitPolicy]:
    """Classify request paths into gateway rate-limit policies.

    Feature routers register below the configured API prefix; the first path
    segment below that prefix selects the policy class. Authentication surfaces
    and platform administration fail closed when the limiter backend is
    unavailable; the general API policy stays available.
    """
    prefix_segments = [segment for segment in settings.api_prefix.split("/") if segment]
    authentication = GatewayRateLimitPolicy(
        "authentication", settings.auth_rate_limit_requests, fail_closed=True
    )
    platform = GatewayRateLimitPolicy(
        "platform", settings.admin_rate_limit_requests, fail_closed=True
    )
    api = GatewayRateLimitPolicy("api", settings.api_rate_limit_requests, fail_closed=False)

    def policy_for_path(path: str) -> GatewayRateLimitPolicy:
        segments = [segment for segment in path.split("/") if segment]
        if segments[: len(prefix_segments)] != prefix_segments:
            return api
        scoped = segments[len(prefix_segments) :]
        if not scoped:
            return api
        if scoped[0] == AUTHENTICATION_PATH_SEGMENT:
            return authentication
        if scoped[0] == PLATFORM_ADMIN_PATH_SEGMENT:
            return platform
        return api

    return policy_for_path


def gateway_identifier(
    request: Request, policy: GatewayRateLimitPolicy, *, settings: Settings
) -> str:
    # Unauthenticated session headers cannot be used to reset gateway limits.
    client_ip = resolve_client_ip(
        request,
        trusted_proxy_hops=settings.trusted_proxy_hops,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    return sha256(f"{policy.name}|{client_ip or 'unknown'}".encode()).hexdigest()
