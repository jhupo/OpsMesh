from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias

from redis import Redis

from backend.app.redis.keys import RedisKeyBuilder

JsonValue: TypeAlias = (
    Mapping[str, "JsonValue"]
    | Sequence["JsonValue"]
    | str
    | int
    | float
    | bool
    | None
)


@dataclass(frozen=True)
class CachedValue:
    key: str
    found: bool
    value: JsonValue = None


class RedisJsonCache:
    _DEFAULT_LOCK_TTL_SECONDS = 30
    _DEFAULT_LOCK_WAIT_TIMEOUT_SECONDS = 5.0
    _DEFAULT_LOCK_RETRY_INTERVAL_SECONDS = 0.025

    def __init__(
        self,
        redis: Redis[str],
        keys: RedisKeyBuilder,
        *,
        namespace: str,
        default_ttl_seconds: int = 60,
    ) -> None:
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be greater than zero")
        self._redis = redis
        self._keys = keys
        self._namespace = namespace
        self._default_ttl_seconds = default_ttl_seconds

    def get(self, key: str) -> CachedValue:
        storage_key = self._storage_key(key)
        raw_value = self._redis.get(storage_key)
        if raw_value is None:
            return CachedValue(key=storage_key, found=False)
        try:
            return CachedValue(
                key=storage_key,
                found=True,
                value=json.loads(raw_value),
            )
        except json.JSONDecodeError:
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
            json.dumps(value, separators=(",", ":"), sort_keys=True),
            ex=resolved_ttl,
        )
        return storage_key

    def get_or_set(
        self,
        key: str,
        loader: Callable[[], JsonValue],
        *,
        ttl_seconds: int | None = None,
        lock_ttl_seconds: int = _DEFAULT_LOCK_TTL_SECONDS,
        lock_wait_timeout_seconds: float = _DEFAULT_LOCK_WAIT_TIMEOUT_SECONDS,
        lock_retry_interval_seconds: float = _DEFAULT_LOCK_RETRY_INTERVAL_SECONDS,
    ) -> CachedValue:
        self._validate_lock_options(
            lock_ttl_seconds=lock_ttl_seconds,
            lock_wait_timeout_seconds=lock_wait_timeout_seconds,
            lock_retry_interval_seconds=lock_retry_interval_seconds,
        )

        lock_key = self._lock_key(key)
        lock = self._redis.lock(
            lock_key,
            timeout=lock_ttl_seconds,
            sleep=lock_retry_interval_seconds,
            blocking_timeout=lock_wait_timeout_seconds,
        )
        if not lock.acquire():
            raise TimeoutError(f"cache loader lock timed out for key {self._storage_key(key)}")
        try:
            cached = self.get(key)
            if cached.found:
                return cached

            value = loader()
            storage_key = self.set(key, value, ttl_seconds=ttl_seconds)
            return CachedValue(key=storage_key, found=True, value=value)
        finally:
            lock.release()

    def delete(self, key: str) -> int:
        return int(self._redis.delete(self._storage_key(key)))

    def delete_many(self, keys: Iterable[str]) -> int:
        storage_keys = [self._storage_key(key) for key in keys]
        if not storage_keys:
            return 0
        return int(self._redis.delete(*storage_keys))

    def delete_prefix(self, prefix: str) -> int:
        normalized_prefix = self._normalize_key(prefix)
        pattern = self._keys.cache(self._namespace, f"{normalized_prefix}*")
        matched_keys = list(self._redis.scan_iter(pattern))
        if not matched_keys:
            return 0
        return int(self._redis.delete(*matched_keys))

    def delete_namespace(self) -> int:
        pattern = self._keys.cache(self._namespace, "*")
        matched_keys = list(self._redis.scan_iter(pattern))
        if not matched_keys:
            return 0
        return int(self._redis.delete(*matched_keys))

    def _storage_key(self, key: str) -> str:
        normalized_key = self._normalize_key(key)
        return self._keys.cache(self._namespace, normalized_key)

    def _lock_key(self, key: str) -> str:
        normalized_key = self._normalize_key(key)
        return self._join_key("cache-lock", self._namespace, normalized_key)

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

    def _join_key(self, *parts: str) -> str:
        clean_parts = [self._keys.prefix.strip(":")]
        clean_parts.extend(part.strip(":") for part in parts if part)
        return ":".join(clean_parts)
