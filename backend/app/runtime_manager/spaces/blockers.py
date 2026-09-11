from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.policies.blocked_reasons import explain_blocked_reason
from backend.app.tasks.models import Task, TaskStep


class RuntimeSpaceBlockerService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def clear_pause_blocks(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> int:
        return self.clear_blocks(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            codes={"runtime_space_paused"},
        )

    def clear_blocks(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        codes: set[str],
    ) -> int:
        steps = self._session.scalars(
            select(TaskStep)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
                (
                    (TaskStep.runtime_space_id == runtime_space_id)
                    | (
                        TaskStep.runtime_space_id.is_(None)
                        & (Task.runtime_space_id == runtime_space_id)
                    )
                ),
            )
        ).all()
        cleared = 0
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            if explanation.code not in codes:
                continue
            updated = dict(dependencies)
            updated.pop("scheduling_status", None)
            updated.pop("blocked_reason", None)
            updated.pop("blocked_resource_keys", None)
            updated.pop("priority_score", None)
            step.dependencies = updated
            cleared += 1
        return cleared
