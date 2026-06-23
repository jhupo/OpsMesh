from uuid import UUID

from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue

_SUPPORTED_MEMORY_INDEX_SOURCES = frozenset({"task", "workspace_file", "artifact"})


def enqueue_workspace_memory_index_job(
    *,
    queue: RedisQueue,
    workspace_id: UUID,
    source_type: str,
    source_id: UUID,
    requested_by_user_id: UUID | None = None,
    requested_by_agent_run_id: UUID | None = None,
    priority: int = -10,
    routing: dict[str, object] | None = None,
) -> bool:
    if source_type not in _SUPPORTED_MEMORY_INDEX_SOURCES:
        raise ValueError(f"Unsupported memory index source_type: {source_type}")

    return queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.MEMORY_INDEX,
            resource_id=source_id,
            requested_by_user_id=requested_by_user_id,
            requested_by_agent_run_id=requested_by_agent_run_id,
            idempotency_key=f"memory.index:{workspace_id}:{source_type}:{source_id}",
            routing={**(routing or {}), "source_type": source_type},
            priority=priority,
        )
    )
