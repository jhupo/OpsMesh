import fakeredis
import pytest
from redis.exceptions import ResponseError

from backend.app.redis.locks import RedisLock, redis_lock


def test_redis_lock_acquires_and_releases_with_token_and_fencing() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    first = RedisLock(redis=redis, key="lock:test", ttl_seconds=60, token="first")
    second = RedisLock(redis=redis, key="lock:test", ttl_seconds=60, token="second")
    third = RedisLock(redis=redis, key="lock:test", ttl_seconds=60, token="third")

    assert first.acquire() is True
    assert first.fencing_token == 1
    assert second.acquire() is False
    assert second.fencing_token is None
    assert second.release() is False
    assert redis.get("lock:test") == "first"
    assert first.release() is True
    assert redis.get("lock:test") is None
    assert third.acquire() is True
    assert third.fencing_token == 2
    assert redis.get("lock:test:fencing") == "2"


def test_redis_lock_release_does_not_delete_new_owner() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    stale = RedisLock(redis=redis, key="lock:test", ttl_seconds=60, token="stale")
    current = RedisLock(redis=redis, key="lock:test", ttl_seconds=60, token="current")

    redis.set("lock:test", current.token)

    assert stale.release() is False
    assert redis.get("lock:test") == current.token
    assert current.release() is True
    assert redis.get("lock:test") is None


def test_redis_lock_extend_refreshes_ttl_for_current_token() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    lock = RedisLock(redis=redis, key="lock:test", ttl_seconds=30, token="owner")

    assert lock.acquire() is True
    assert redis.ttl("lock:test") == 30
    assert lock.extend(120) is True
    assert redis.ttl("lock:test") == 120
    assert lock.refresh(90) is True
    assert redis.ttl("lock:test") == 90


def test_redis_lock_extend_fails_for_wrong_token() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    stale = RedisLock(redis=redis, key="lock:test", ttl_seconds=30, token="stale")
    current = RedisLock(redis=redis, key="lock:test", ttl_seconds=30, token="current")

    assert current.acquire() is True
    assert stale.extend(120) is False
    assert redis.get("lock:test") == "current"
    assert redis.ttl("lock:test") == 30


def test_redis_lock_extend_falls_back_when_eval_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    lock = RedisLock(redis=redis, key="lock:test", ttl_seconds=30, token="owner")
    assert lock.acquire() is True

    def raise_unsupported_eval(*args: object) -> None:
        raise ResponseError("unknown command 'eval'")

    monkeypatch.setattr(redis, "eval", raise_unsupported_eval)

    assert lock.extend(120) is True
    assert redis.ttl("lock:test") == 120


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


def test_redis_lock_context_manager_can_yield_lock_object() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)

    with redis_lock(redis, "lock:test", 60, yield_lock=True) as lock:
        assert isinstance(lock, RedisLock)
        assert lock.acquired is True
        assert lock.fencing_token == 1
        assert lock.extend(120) is True

    assert redis.get("lock:test") is None
