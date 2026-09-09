from fastapi.testclient import TestClient
from limits.storage import MemoryStorage
from starlette.requests import Request

from backend.app.core.client_ip import resolve_client_ip
from backend.app.core.config import Settings
from backend.app.main import create_app_with_dependencies
from backend.app.rate_limits.service import FixedWindowRateLimiter


def test_fixed_window_limiter_blocks_after_limit() -> None:
    limiter = FixedWindowRateLimiter(MemoryStorage())

    first = limiter.check(identifier="client-1", limit=2, window_seconds=60)
    second = limiter.check(identifier="client-1", limit=2, window_seconds=60)
    third = limiter.check(identifier="client-1", limit=2, window_seconds=60)

    assert first.allowed is True
    assert first.backend_available is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert third.allowed is False
    assert third.remaining == 0


def test_rate_limit_middleware_returns_429_with_headers() -> None:
    app = create_app_with_dependencies(
        settings=Settings(
            environment="test",
            log_format="text",
            api_rate_limit_enabled=True,
            api_rate_limit_requests=1,
            api_rate_limit_window_seconds=60,
        ),
        rate_limiter=FixedWindowRateLimiter(MemoryStorage()),
    )
    client = TestClient(app)

    allowed = client.get("/api/v1/health")
    limited = client.get("/api/v1/health")

    assert allowed.status_code == 200
    assert allowed.headers["X-RateLimit-Remaining"] == "0"
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["X-RateLimit-Limit"] == "1"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert limited.headers["Retry-After"]
    assert limited.headers["X-Request-ID"]
    assert limited.headers["X-Content-Type-Options"] == "nosniff"


def test_rate_limiter_fails_open_when_redis_is_unavailable() -> None:
    limiter = FixedWindowRateLimiter(BrokenStorage())

    decision = limiter.check(identifier="client-1", limit=1, window_seconds=60)

    assert decision.allowed is True
    assert decision.backend_available is False
    assert decision.remaining == 1


def test_sensitive_gateway_routes_fail_closed_when_redis_is_unavailable() -> None:
    app = create_app_with_dependencies(
        settings=Settings(
            environment="test",
            log_format="text",
            api_rate_limit_enabled=True,
        ),
        rate_limiter=FixedWindowRateLimiter(BrokenStorage()),
    )
    client = TestClient(app)

    auth_response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "password"},
    )
    health_response = client.get("/api/v1/health")

    assert auth_response.status_code == 503
    assert auth_response.json()["error"]["code"] == "rate_limit_unavailable"
    assert health_response.status_code == 200


def test_client_ip_ignores_forwarded_header_until_proxy_trust_is_configured() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 10.0.0.1")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )

    assert resolve_client_ip(request) == "127.0.0.1"
    assert resolve_client_ip(request, trusted_proxy_hops=1) == "10.0.0.1"
    assert resolve_client_ip(request, trusted_proxy_hops=2) == "203.0.113.10"


class BrokenStorage(MemoryStorage):
    def incr(self, key: str, expiry: float, amount: int = 1) -> int:
        raise ConnectionError("redis down")
