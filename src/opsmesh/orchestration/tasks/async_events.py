"""Read the existing Task event stream using Redis's native async transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from redis.asyncio import Redis

from opsmesh.orchestration.tasks.events import (
    RedisStreamEntries,
    TaskEvent,
    decode_stream_entries,
)


@dataclass(frozen=True)
class AsyncRedisTaskEventBus:
    redis: Redis[str]
    key_prefix: str = "opsmesh"

    def _key(self, workspace_id: UUID, task_id: UUID) -> str:
        return f"{self.key_prefix.strip(':')}:workspace:{workspace_id}:task:{task_id}:events"

    async def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        block_ms: int = 0,
    ) -> list[TaskEvent]:
        entries = await self.redis.xread(
            {self._key(workspace_id, task_id): after_id},
            count=100,
            **({"block": block_ms} if block_ms > 0 else {}),
        )
        return decode_stream_entries(cast(RedisStreamEntries, entries))

    async def contains_cursor(self, workspace_id: UUID, task_id: UUID, cursor: str) -> bool:
        return bool(
            await self.redis.xrange(self._key(workspace_id, task_id), cursor, cursor, count=1)
        )
