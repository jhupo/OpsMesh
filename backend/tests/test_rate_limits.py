import fakeredis
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app_with_dependencies
from backend.app.rate_limits.service import RedisFixedWindowRateLimiter


def test_redis_fixed_window_limiter_blocks_after_limit() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    limiter = RedisFixedWindowRateLimiter(redis, key_prefix="opsmesh", clock=lambda: 120.0)

    first = limiter.check(identifier="client-1", limit=2, window_seconds=60)
    second = limiter.check(identifier="client-1", limit=2, window_seconds=60)
    third = limiter.check(identifier="client-1", limit=2, window_seconds=60)

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert third.allowed is False
    assert third.remaining == 0


def test_rate_limit_middleware_returns_429_with_headers() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    app = create_app_with_dependencies(
        settings=Settings(
            environment="test",
            log_format="text",
            api_rate_limit_enabled=True,
            api_rate_limit_requests=1,
            api_rate_limit_window_seconds=60,
        ),
        rate_limiter=RedisFixedWindowRateLimiter(redis, key_prefix="opsmesh"),
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


def test_rate_limiter_fails_open_when_redis_is_unavailable() -> None:
    limiter = RedisFixedWindowRateLimiter(BrokenRedis(), key_prefix="opsmesh")

    decision = limiter.check(identifier="client-1", limit=1, window_seconds=60)

    assert decision.allowed is True
    assert decision.remaining == 1


class BrokenRedis:
    def incr(self, key: str) -> int:
        raise ConnectionError("redis down")
