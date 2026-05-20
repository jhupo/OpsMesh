from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
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
    ) -> CachedValue:
        cached = self.get(key)
        if cached.found:
            return cached

        value = loader()
        storage_key = self.set(key, value, ttl_seconds=ttl_seconds)
        return CachedValue(key=storage_key, found=True, value=value)

    def delete(self, key: str) -> int:
        return int(self._redis.delete(self._storage_key(key)))

    def delete_namespace(self) -> int:
        pattern = self._keys.cache(self._namespace, "*")
        matched_keys = list(self._redis.scan_iter(pattern))
        if not matched_keys:
            return 0
        return int(self._redis.delete(*matched_keys))

    def _storage_key(self, key: str) -> str:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("cache key must not be empty")
        return self._keys.cache(self._namespace, normalized_key)

    def _resolve_ttl(self, ttl_seconds: int | None) -> int:
        resolved_ttl = self._default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if resolved_ttl <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        return resolved_ttl
