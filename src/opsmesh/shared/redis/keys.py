"""Explicit tenant- and resource-scoped Redis key construction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RedisKeyBuilder:
    prefix: str

    def cache(self, namespace: str, key: str) -> str:
        return self._join("cache", namespace, key)

    def cache_lock(self, namespace: str, key: str) -> str:
        return self._join("cache-lock", namespace, key)

    def cache_generation(self, namespace: str, key: str) -> str:
        return self._join("cache-generation", namespace, key)

    def _join(self, *parts: str) -> str:
        clean_parts = [self.prefix.strip(":")]
        clean_parts.extend(part.strip(":") for part in parts if part)
        return ":".join(clean_parts)
