from dataclasses import dataclass


@dataclass(frozen=True)
class RedisKeyBuilder:
    prefix: str

    def queue(self, queue_name: str) -> str:
        return self._join("queue", queue_name)

    def dead_letter_queue(self, queue_name: str) -> str:
        return self._join("queue", queue_name, "dead")

    def workspace_queue(self, workspace_id: str, queue_name: str) -> str:
        return self._join("workspace", workspace_id, "queue", queue_name)

    def run_lock(self, workspace_id: str, run_id: str) -> str:
        return self._join("lock", workspace_id, "run", run_id)

    def idempotency_key(self, workspace_id: str, idempotency_key: str) -> str:
        return self._join("idempotency", workspace_id, idempotency_key)

    def _join(self, *parts: str) -> str:
        clean_parts = [self.prefix.strip(":")]
        clean_parts.extend(part.strip(":") for part in parts if part)
        return ":".join(clean_parts)

