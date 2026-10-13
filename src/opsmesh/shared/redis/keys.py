from dataclasses import dataclass


@dataclass(frozen=True)
class RedisKeyBuilder:
    prefix: str

    def idempotency_key(self, workspace_id: str, idempotency_key: str) -> str:
        return self._join("idempotency", workspace_id, idempotency_key)

    def cache(self, namespace: str, key: str) -> str:
        return self._join("cache", namespace, key)

    def _join(self, *parts: str) -> str:
        clean_parts = [self.prefix.strip(":")]
        clean_parts.extend(part.strip(":") for part in parts if part)
        return ":".join(clean_parts)
