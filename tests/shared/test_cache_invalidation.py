"""S2 cache tests: single-loader publication bounded by the invalidation generation."""

from __future__ import annotations

import os
import threading
import time
from typing import cast
from uuid import uuid4

import fakeredis
import pytest
from redis import Redis

from opsmesh.shared.redis.cache import DEFAULT_GENERATION_TTL_SECONDS, RedisJsonCache
from opsmesh.shared.redis.keys import RedisKeyBuilder

REDIS_URL_ENV = "OPSMESH_TEST_REDIS_URL"


def cache() -> RedisJsonCache:
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    return RedisJsonCache(
        cast("object", redis_client),
        RedisKeyBuilder("opsmesh"),
        namespace="test",
        default_ttl_seconds=60,
    )


def test_get_or_set_publishes_the_loaded_value() -> None:
    cache_instance = cache()
    result = cache_instance.get_or_set("key", lambda: {"value": 1})
    assert result.found is True
    assert result.value == {"value": 1}
    assert cache_instance.get("key").value == {"value": 1}


def test_invalidation_during_load_never_publishes_the_stale_value() -> None:
    cache_instance = cache()
    started = threading.Event()
    release = threading.Event()

    def loader() -> dict[str, object]:
        started.set()
        release.wait(timeout=5)
        return {"value": "stale"}

    def invalidate() -> None:
        started.wait(timeout=5)
        cache_instance.delete("key")
        release.set()

    thread = threading.Thread(target=invalidate)
    thread.start()
    try:
        result = cache_instance.get_or_set("key", loader)
    finally:
        thread.join(timeout=5)
    assert result.value == {"value": "stale"}
    assert cache_instance.get("key").found is False, (
        "a value loaded before invalidation must not persist"
    )


def test_delete_bounds_the_generation_key_with_a_ttl() -> None:
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    cache_instance = RedisJsonCache(
        cast("object", redis_client),
        RedisKeyBuilder("opsmesh"),
        namespace="test",
        default_ttl_seconds=60,
    )
    generation_key = RedisKeyBuilder("opsmesh").cache_generation("test", "absent")
    cache_instance.delete("absent")
    ttl = redis_client.ttl(generation_key)
    assert 0 < ttl <= DEFAULT_GENERATION_TTL_SECONDS, (
        "an invalidation must leave a bounded generation key, never a permanent one"
    )


def test_expired_generation_does_not_unblock_a_stale_loader() -> None:
    """A generation key that expires and is recreated must not reissue the token
    a stale loader holds, otherwise the stale value would be published."""
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    keys = RedisKeyBuilder("opsmesh")
    cache_instance = RedisJsonCache(
        cast("object", redis_client), keys, namespace="test", default_ttl_seconds=60
    )
    generation_key = keys.cache_generation("test", "key")
    started = threading.Event()
    release = threading.Event()

    def loader() -> dict[str, object]:
        started.set()
        release.wait(timeout=5)
        return {"value": "stale"}

    def invalidate() -> None:
        started.wait(timeout=5)
        cache_instance.delete("key")
        # Redis recycles the expired generation key; the next delete recreates it.
        redis_client.delete(generation_key)
        cache_instance.delete("key")
        release.set()

    thread = threading.Thread(target=invalidate)
    thread.start()
    try:
        result = cache_instance.get_or_set("key", loader)
    finally:
        thread.join(timeout=5)
    assert result.value == {"value": "stale"}
    assert cache_instance.get("key").found is False, (
        "a recycled generation must not reissue the token a stale loader holds"
    )


def test_publication_is_blocked_when_the_generation_token_expires() -> None:
    """With a short generation TTL the token can expire before publication. The
    loader holds a concrete token, so a missing token must fail the check
    instead of matching an empty default."""
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    keys = RedisKeyBuilder("opsmesh")
    cache_instance = RedisJsonCache(
        cast("object", redis_client),
        keys,
        namespace="test",
        default_ttl_seconds=60,
        generation_ttl_seconds=1,
    )
    generation_key = keys.cache_generation("test", "key")
    started = threading.Event()
    release = threading.Event()

    def loader() -> dict[str, object]:
        started.set()
        release.wait(timeout=5)
        return {"value": "stale"}

    def invalidate() -> None:
        started.wait(timeout=5)
        cache_instance.delete("key")
        # The short-TTL generation token expires before the loader publishes.
        redis_client.delete(generation_key)
        release.set()

    thread = threading.Thread(target=invalidate)
    thread.start()
    try:
        result = cache_instance.get_or_set("key", loader)
    finally:
        thread.join(timeout=5)
    assert result.value == {"value": "stale"}
    assert cache_instance.get("key").found is False, (
        "an expired generation token must not read as an unchanged empty token"
    )


def test_value_loaded_after_invalidation_is_published() -> None:
    cache_instance = cache()
    cache_instance.delete("key")
    result = cache_instance.get_or_set("key", lambda: {"value": "fresh"})
    assert result.value == {"value": "fresh"}
    assert cache_instance.get("key").value == {"value": "fresh"}


def test_delete_removes_the_stored_value() -> None:
    cache_instance = cache()
    cache_instance.set("key", {"value": 1})
    assert cache_instance.delete("key") == 1
    assert cache_instance.get("key").found is False


def test_invalidation_guard_holds_against_real_redis() -> None:
    """Opt-in real-Redis check of the same generation guard (never SQLite or mocks)."""
    url = os.environ.get(REDIS_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{REDIS_URL_ENV} is not configured")
    key = f"invalidation-{uuid4()}"
    redis_client: Redis[str] = Redis.from_url(url, decode_responses=True)
    cache_instance = RedisJsonCache(
        redis_client,
        RedisKeyBuilder("opsmesh-test"),
        namespace="real",
        default_ttl_seconds=60,
    )
    started = threading.Event()
    release = threading.Event()

    def loader() -> dict[str, object]:
        started.set()
        release.wait(timeout=5)
        return {"value": "stale"}

    def invalidate() -> None:
        started.wait(timeout=5)
        cache_instance.delete(key)
        release.set()

    thread = threading.Thread(target=invalidate)
    thread.start()
    try:
        result = cache_instance.get_or_set(key, loader)
    finally:
        thread.join(timeout=5)
    # Assert before any cleanup: deleting the key here would hide a stale value
    # that the guard failed to keep out of the cache.
    assert result.value == {"value": "stale"}
    assert cache_instance.get(key).found is False, (
        "a value loaded before invalidation must not persist against real Redis"
    )
    generation_key = RedisKeyBuilder("opsmesh-test").cache_generation("real", key)
    generation_ttl = redis_client.ttl(generation_key)
    assert 0 < generation_ttl <= DEFAULT_GENERATION_TTL_SECONDS, (
        "an invalidation must leave a bounded generation key against real Redis"
    )
    first_generation = redis_client.get(generation_key)
    cache_instance.delete(key)
    second_generation = redis_client.get(generation_key)
    assert first_generation != second_generation, (
        "a recreated generation must issue a fresh token, never repeat the old one"
    )
    cache_instance.delete(key)
    redis_client.close()


def test_invalidation_guard_survives_a_short_generation_ttl_against_real_redis() -> None:
    """Opt-in real-Redis check with generation_ttl_seconds=1: the generation
    token really expires before the loader publishes, and the stale value must
    still not persist (the exact short-TTL repro from review)."""
    url = os.environ.get(REDIS_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{REDIS_URL_ENV} is not configured")
    key = f"short-ttl-{uuid4()}"
    redis_client: Redis[str] = Redis.from_url(url, decode_responses=True)
    cache_instance = RedisJsonCache(
        redis_client,
        RedisKeyBuilder("opsmesh-test"),
        namespace="real",
        default_ttl_seconds=60,
        generation_ttl_seconds=1,
    )
    started = threading.Event()
    release = threading.Event()

    def loader() -> dict[str, object]:
        started.set()
        release.wait(timeout=5)
        return {"value": "stale"}

    def invalidate() -> None:
        started.wait(timeout=5)
        cache_instance.delete(key)
        time.sleep(1.2)  # the generation token expires before publication
        release.set()

    thread = threading.Thread(target=invalidate)
    thread.start()
    try:
        result = cache_instance.get_or_set(key, loader)
    finally:
        thread.join(timeout=5)
    # Assert before any cleanup: deleting the key here would hide a stale value.
    assert result.value == {"value": "stale"}
    assert cache_instance.get(key).found is False, (
        "an expired generation token must not let a stale value persist against real Redis"
    )
    cache_instance.delete(key)
    redis_client.close()
