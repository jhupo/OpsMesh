import fakeredis
import pytest

from backend.app.redis.cache import RedisJsonCache
from backend.app.redis.keys import RedisKeyBuilder


def test_cache_set_and_get_json_value_with_prefixed_key() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("chaincloud"), namespace="operations")

    storage_key = cache.set("overview:workspace-1", {"running": 3, "blocked": False})
    cached = cache.get("overview:workspace-1")

    assert storage_key == "chaincloud:cache:operations:overview:workspace-1"
    assert cached.found is True
    assert cached.value == {"running": 3, "blocked": False}


def test_cache_returns_miss_for_missing_key() -> None:
    cache = RedisJsonCache(
        fakeredis.FakeRedis(decode_responses=True),
        RedisKeyBuilder("chaincloud"),
        namespace="operations",
    )

    cached = cache.get("missing")

    assert cached.found is False
    assert cached.value is None


def test_cache_uses_default_and_explicit_ttl() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(
        redis,
        RedisKeyBuilder("chaincloud"),
        namespace="operations",
        default_ttl_seconds=90,
    )

    default_key = cache.set("default-ttl", {"ok": True})
    explicit_key = cache.set("explicit-ttl", {"ok": True}, ttl_seconds=12)

    assert redis.ttl(default_key) == 90
    assert redis.ttl(explicit_key) == 12


def test_cache_get_or_set_only_calls_loader_on_miss() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("chaincloud"), namespace="operations")
    calls = 0

    def load_value() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"value": calls}

    first = cache.get_or_set("expensive", load_value)
    second = cache.get_or_set("expensive", load_value)

    assert first.value == {"value": 1}
    assert second.value == {"value": 1}
    assert calls == 1


def test_cache_handles_json_null_as_cached_value() -> None:
    cache = RedisJsonCache(
        fakeredis.FakeRedis(decode_responses=True),
        RedisKeyBuilder("chaincloud"),
        namespace="operations",
    )

    cache.set("nullable", None)
    cached = cache.get("nullable")

    assert cached.found is True
    assert cached.value is None


def test_cache_deletes_corrupt_json_and_returns_miss() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("chaincloud"), namespace="operations")
    redis.set("chaincloud:cache:operations:broken", "{")

    cached = cache.get("broken")

    assert cached.found is False
    assert redis.get("chaincloud:cache:operations:broken") is None


def test_cache_delete_namespace_only_removes_matching_namespace() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    operations_cache = RedisJsonCache(redis, RedisKeyBuilder("chaincloud"), namespace="operations")
    tasks_cache = RedisJsonCache(redis, RedisKeyBuilder("chaincloud"), namespace="tasks")
    operations_cache.set("one", {"value": 1})
    operations_cache.set("two", {"value": 2})
    tasks_cache.set("one", {"value": 1})

    removed = operations_cache.delete_namespace()

    assert removed == 2
    assert operations_cache.get("one").found is False
    assert tasks_cache.get("one").found is True


def test_cache_rejects_invalid_ttl_and_empty_keys() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)

    with pytest.raises(ValueError, match="default_ttl_seconds"):
        RedisJsonCache(
            redis,
            RedisKeyBuilder("chaincloud"),
            namespace="operations",
            default_ttl_seconds=0,
        )

    cache = RedisJsonCache(redis, RedisKeyBuilder("chaincloud"), namespace="operations")

    with pytest.raises(ValueError, match="ttl_seconds"):
        cache.set("invalid-ttl", {"ok": True}, ttl_seconds=0)

    with pytest.raises(ValueError, match="cache key"):
        cache.get(" ")
