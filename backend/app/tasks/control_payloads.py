from __future__ import annotations

from uuid import UUID

from backend.app.api.schemas.tasks import TaskControlActionRequest
from backend.app.tasks.models import Task


def task_control_response(
    task: Task,
    *,
    request: TaskControlActionRequest,
    status: str,
    message_id: UUID | None = None,
    changed_step_ids: list[UUID] | None = None,
    scheduled_run_ids: list[UUID] | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "workspace_id": task.workspace_id,
        "task_id": task.id,
        "action": request.action,
        "status": status,
        "task_status": task.status,
        "message_id": message_id,
        "changed_step_ids": changed_step_ids or [],
        "scheduled_run_ids": scheduled_run_ids or [],
        "details": details or {},
    }
