import fakeredis

from backend.app.redis.locks import RedisLock, redis_lock


def test_redis_lock_acquires_and_releases_with_token() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    first = RedisLock(redis=redis, key="lock:test", ttl_seconds=60, token="first")
    second = RedisLock(redis=redis, key="lock:test", ttl_seconds=60, token="second")

    assert first.acquire() is True
    assert second.acquire() is False
    assert second.release() is False
    assert redis.get("lock:test") == "first"
    assert first.release() is True
    assert redis.get("lock:test") is None


def test_redis_lock_context_manager_releases_only_when_acquired() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)

    with (
        redis_lock(redis, "lock:test", 60) as first,
        redis_lock(redis, "lock:test", 60) as second,
    ):
        assert first is True
        assert second is False
        assert redis.get("lock:test") is not None

    assert redis.get("lock:test") is None
