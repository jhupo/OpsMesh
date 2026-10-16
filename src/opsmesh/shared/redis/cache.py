"""Disposable JSON cache with TTL, single-loader publication and invalidation."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from redis import Redis
from redis.exceptions import LockError, LockNotOwnedError

from opsmesh.shared.contracts import JsonValue
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.shared.serialization import json_safe_value

logger = logging.getLogger(__name__)
DEFAULT_CACHE_TTL_SECONDS = 60
DEFAULT_LOCK_TTL_SECONDS = 30
DEFAULT_LOCK_WAIT_TIMEOUT_SECONDS = 5.0
DEFAULT_LOCK_RETRY_INTERVAL_SECONDS = 0.025
# The generation key must outlive any in-flight loader lease, otherwise an
# expired generation would read as "0" and let a stale load publish. It is
# still bounded so invalidated keys never accumulate: 24h far exceeds the
# default 30s loader lease, and the loader lease itself bounds publication.
DEFAULT_GENERATION_TTL_SECONDS = 86_400


@dataclass(frozen=True)
class CachedValue:
    key: str
    found: bool
    value: JsonValue = None


class RedisJsonCache:
    """Single-loader cache whose publication is bounded by the loader lease and
    the invalidation generation observed before loading.

    ``delete`` advances the generation first, so a loader that started before
    the invalidation can never publish its (now stale) result afterwards. The
    generation key carries a bounded TTL, so invalidations never accumulate
    keys for entries that no longer exist.
    """

    _PUBLISH_SCRIPT = """
        if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
        local generation = redis.call('get', KEYS[3])
        if generation == false then generation = '0' end
        if generation ~= ARGV[2] then return 2 end
        redis.call('set', KEYS[2], ARGV[3], 'EX', ARGV[4])
        return 1
    """
    _PUBLISH_LOCK_LOST = 0
    _PUBLISH_INVALIDATED = 2
    _PUBLISHED = 1
    _INVALIDATE_SCRIPT = """
        local generation = redis.call('get', KEYS[1])
        if generation == false then
            redis.call('set', KEYS[1], '1')
        else
            redis.call('incr', KEYS[1])
        end
        redis.call('expire', KEYS[1], ARGV[1])
        return redis.call('del', KEYS[2])
    """

    def __init__(
        self,
        redis: Redis[str],
        keys: RedisKeyBuilder,
        *,
        namespace: str,
        default_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        generation_ttl_seconds: int = DEFAULT_GENERATION_TTL_SECONDS,
    ) -> None:
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be greater than zero")
        if generation_ttl_seconds <= 0:
            raise ValueError("generation_ttl_seconds must be greater than zero")
        self._redis = redis
        self._keys = keys
        self._namespace = namespace
        self._default_ttl_seconds = default_ttl_seconds
        self._generation_ttl_seconds = generation_ttl_seconds
        self._publish = redis.register_script(self._PUBLISH_SCRIPT)
        self._invalidate = redis.register_script(self._INVALIDATE_SCRIPT)

    def get(self, key: str) -> CachedValue:
        storage_key = self._storage_key(key)
        raw_value = self._redis.get(storage_key)
        if raw_value is None:
            return CachedValue(key=storage_key, found=False)
        try:
            parsed = json.loads(raw_value)
            json_safe_value(parsed)
            return CachedValue(
                key=storage_key,
                found=True,
                value=parsed,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            self._redis.delete(storage_key)
            return CachedValue(key=storage_key, found=False)

    def set(
        self,
        key: str,
        value: JsonValue,
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        resolved_ttl = self._resolve_ttl(ttl_seconds)
        storage_key = self._storage_key(key)
        self._redis.set(
            storage_key,
            json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False),
            ex=resolved_ttl,
        )
        return storage_key

    def get_or_set(
        self,
        key: str,
        loader: Callable[[], JsonValue],
        *,
        ttl_seconds: int | None = None,
        lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
        lock_wait_timeout_seconds: float = DEFAULT_LOCK_WAIT_TIMEOUT_SECONDS,
        lock_retry_interval_seconds: float = DEFAULT_LOCK_RETRY_INTERVAL_SECONDS,
    ) -> CachedValue:
        self._validate_lock_options(
            lock_ttl_seconds=lock_ttl_seconds,
            lock_wait_timeout_seconds=lock_wait_timeout_seconds,
            lock_retry_interval_seconds=lock_retry_interval_seconds,
        )
        resolved_ttl = self._resolve_ttl(ttl_seconds)
        cached = self.get(key)
        if cached.found:
            return cached

        lock_key = self._lock_key(key)
        lock = self._redis.lock(
            lock_key,
            timeout=lock_ttl_seconds,
            sleep=lock_retry_interval_seconds,
            blocking_timeout=lock_wait_timeout_seconds,
        )
        token = uuid4().hex
        if not lock.acquire(token=token):
            raise TimeoutError(f"cache loader lock timed out for key {self._storage_key(key)}")
        try:
            cached = self.get(key)
            if cached.found:
                return cached

            generation = self._generation(key)
            value = loader()
            storage_key = self._storage_key(key)
            serialized = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)
            published = self._publish(
                keys=[lock_key, storage_key, self._generation_key(key)],
                args=[token, generation, serialized, resolved_ttl],
            )
            if published == self._PUBLISH_INVALIDATED:
                # The entry was invalidated while loading: the loaded value is
                # served for this call only and never persisted.
                return CachedValue(key=storage_key, found=True, value=value)
            if published != self._PUBLISHED:
                raise LockNotOwnedError("Cache loader lease expired before publication")
            return CachedValue(key=storage_key, found=True, value=value)
        finally:
            failure = sys.exception()
            try:
                lock.release()
            except LockError:
                logger.warning(
                    "Cache loader lease expired during cleanup",
                    extra={"loader_failed": failure is not None},
                )

    def delete(self, key: str) -> int:
        # Invalidate before deleting so a concurrent loader cannot publish the
        # pre-delete value after the delete completes. One script advances the
        # generation, bounds its TTL and removes the entry atomically.
        return int(
            self._invalidate(
                keys=[self._generation_key(key), self._storage_key(key)],
                args=[self._generation_ttl_seconds],
            )
        )

    def _generation(self, key: str) -> str:
        current = self._redis.get(self._generation_key(key))
        return current if current is not None else "0"

    def _generation_key(self, key: str) -> str:
        normalized_key = self._normalize_key(key)
        return self._keys.cache_generation(self._namespace, normalized_key)

    def _storage_key(self, key: str) -> str:
        normalized_key = self._normalize_key(key)
        return self._keys.cache(self._namespace, normalized_key)

    def _lock_key(self, key: str) -> str:
        normalized_key = self._normalize_key(key)
        return self._keys.cache_lock(self._namespace, normalized_key)

    def _normalize_key(self, key: str) -> str:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("cache key must not be empty")
        return normalized_key

    def _resolve_ttl(self, ttl_seconds: int | None) -> int:
        resolved_ttl = self._default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if resolved_ttl <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        return resolved_ttl

    def _validate_lock_options(
        self,
        *,
        lock_ttl_seconds: int,
        lock_wait_timeout_seconds: float,
        lock_retry_interval_seconds: float,
    ) -> None:
        if lock_ttl_seconds <= 0:
            raise ValueError("lock_ttl_seconds must be greater than zero")
        if lock_wait_timeout_seconds <= 0:
            raise ValueError("lock_wait_timeout_seconds must be greater than zero")
        if lock_retry_interval_seconds <= 0:
            raise ValueError("lock_retry_interval_seconds must be greater than zero")
