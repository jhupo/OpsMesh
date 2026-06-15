from __future__ import annotations

from uuid import UUID

from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


def enqueue_team_execution_loop_job(
    *,
    queue: RedisQueue,
    workspace_id: UUID,
    team_id: UUID,
    requested_by_user_id: UUID | None,
    idempotency_suffix: str,
    priority: int = 0,
    routing: dict[str, object] | None = None,
) -> bool:
    return queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.TEAM_EXECUTION_LOOP,
            resource_id=team_id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=(
                f"team.execution_loop:{workspace_id}:{team_id}:{idempotency_suffix}"
            ),
            priority=priority,
            routing=routing or {},
        )
    )
