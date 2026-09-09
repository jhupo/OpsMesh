from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import fakeredis
import pytest

from backend.app.redis.cache import RedisJsonCache
from backend.app.redis.keys import RedisKeyBuilder


def test_cache_set_and_get_json_value_with_prefixed_key() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")

    storage_key = cache.set("overview:workspace-1", {"running": 3, "blocked": False})
    cached = cache.get("overview:workspace-1")

    assert storage_key == "opsmesh:cache:operations:overview:workspace-1"
    assert cached.found is True
    assert cached.value == {"running": 3, "blocked": False}


def test_cache_returns_miss_for_missing_key() -> None:
    cache = RedisJsonCache(
        fakeredis.FakeRedis(decode_responses=True),
        RedisKeyBuilder("opsmesh"),
        namespace="operations",
    )

    cached = cache.get("missing")

    assert cached.found is False
    assert cached.value is None


def test_cache_uses_default_and_explicit_ttl() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(
        redis,
        RedisKeyBuilder("opsmesh"),
        namespace="operations",
        default_ttl_seconds=90,
    )

    default_key = cache.set("default-ttl", {"ok": True})
    explicit_key = cache.set("explicit-ttl", {"ok": True}, ttl_seconds=12)

    assert redis.ttl(default_key) == 90
    assert redis.ttl(explicit_key) == 12


def test_cache_get_or_set_only_calls_loader_on_miss() -> None:
    redis = _LockingFakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")
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


def test_cache_get_or_set_single_flight_only_runs_loader_once_on_concurrent_miss() -> None:
    redis = _LockingFakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")
    loader_started = Event()
    release_loader = Event()
    calls = 0
    calls_lock = Lock()

    def load_value() -> dict[str, int]:
        nonlocal calls
        with calls_lock:
            calls += 1
        loader_started.set()
        assert release_loader.wait(timeout=2)
        return {"value": 42}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(cache.get_or_set, "expensive-concurrent", load_value)
            for _ in range(2)
        ]

        assert loader_started.wait(timeout=2)
        release_loader.set()
        results = [future.result(timeout=2) for future in futures]

    assert [result.value for result in results] == [{"value": 42}, {"value": 42}]
    assert calls == 1


def test_cache_get_or_set_releases_lock_and_does_not_cache_loader_exception() -> None:
    redis = _LockingFakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")
    calls = 0

    def fail_to_load() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        raise RuntimeError("loader failed")

    with pytest.raises(RuntimeError, match="loader failed"):
        cache.get_or_set("exceptional", fail_to_load)

    assert cache.get("exceptional").found is False

    recovered = cache.get_or_set("exceptional", lambda: {"ok": True})

    assert recovered.value == {"ok": True}
    assert calls == 1


def test_cache_handles_json_null_as_cached_value() -> None:
    cache = RedisJsonCache(
        fakeredis.FakeRedis(decode_responses=True),
        RedisKeyBuilder("opsmesh"),
        namespace="operations",
    )

    cache.set("nullable", None)
    cached = cache.get("nullable")

    assert cached.found is True
    assert cached.value is None


def test_cache_deletes_corrupt_json_and_returns_miss() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")
    redis.set("opsmesh:cache:operations:broken", "{")

    cached = cache.get("broken")

    assert cached.found is False
    assert redis.get("opsmesh:cache:operations:broken") is None


def test_cache_delete_namespace_only_removes_matching_namespace() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    operations_cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")
    tasks_cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="tasks")
    operations_cache.set("one", {"value": 1})
    operations_cache.set("two", {"value": 2})
    tasks_cache.set("one", {"value": 1})

    removed = operations_cache.delete_namespace()

    assert removed == 2
    assert operations_cache.get("one").found is False
    assert tasks_cache.get("one").found is True


def test_cache_delete_prefix_only_removes_matching_prefix() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")
    tasks_cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="tasks")
    cache.set("workspace:one:overview", {"value": 1})
    cache.set("workspace:one:metrics", {"value": 2})
    cache.set("workspace:two:overview", {"value": 3})
    tasks_cache.set("workspace:one:overview", {"value": 4})

    removed = cache.delete_prefix("workspace:one:")

    assert removed == 2
    assert cache.get("workspace:one:overview").found is False
    assert cache.get("workspace:one:metrics").found is False
    assert cache.get("workspace:two:overview").found is True
    assert tasks_cache.get("workspace:one:overview").found is True


def test_cache_rejects_invalid_ttl_and_empty_keys() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)

    with pytest.raises(ValueError, match="default_ttl_seconds"):
        RedisJsonCache(
            redis,
            RedisKeyBuilder("opsmesh"),
            namespace="operations",
            default_ttl_seconds=0,
        )

    cache = RedisJsonCache(redis, RedisKeyBuilder("opsmesh"), namespace="operations")

    with pytest.raises(ValueError, match="ttl_seconds"):
        cache.set("invalid-ttl", {"ok": True}, ttl_seconds=0)

    with pytest.raises(ValueError, match="cache key"):
        cache.get(" ")


class _LockingFakeRedis(fakeredis.FakeRedis):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._local_locks: dict[str, Lock] = {}
        self._local_locks_guard = Lock()

    def lock(
        self,
        name: str,
        *,
        blocking_timeout: float,
        **_: object,
    ) -> _LocalLock:
        with self._local_locks_guard:
            mutex = self._local_locks.setdefault(name, Lock())
        return _LocalLock(mutex, blocking_timeout=blocking_timeout)


class _LocalLock:
    def __init__(self, mutex: Lock, *, blocking_timeout: float) -> None:
        self._mutex = mutex
        self._blocking_timeout = blocking_timeout

    def acquire(self) -> bool:
        return self._mutex.acquire(timeout=self._blocking_timeout)

    def release(self) -> None:
        self._mutex.release()
