from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.domains.models import RevisionRequest
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep


class RevisionRequestPlanner:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_step(self, task: Task, revision: RevisionRequest) -> TaskStep:
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            assigned_agent_profile_id=revision.assigned_agent_profile_id,
            runtime_space_id=task.runtime_space_id,
            work_package_id=revision_work_package_id(revision),
            title="Revision request",
            description=revision.instruction,
            status="queued",
            order_index=self.next_step_order(task),
            acceptance_criteria=[revision.instruction],
            review_policy={"reviewer": "manager", "mode": "revision_request_review"},
            dependencies={
                "revision_request": {
                    "id": str(revision.id),
                    "domain_item_id": str(revision.domain_item_id)
                    if revision.domain_item_id is not None
                    else None,
                    "payload": revision.payload,
                }
            },
        )
        self._session.add(step)
        self._session.flush([step])
        return step

    def existing_step(self, task: Task, revision: RevisionRequest) -> TaskStep | None:
        return self._session.scalar(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.work_package_id == revision_work_package_id(revision),
            )
        )

    def next_step_order(self, task: Task) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskStep.order_index), 0)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        )
        return int(current or 0) + 1

    def append_task_message(
        self,
        task: Task,
        *,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type=message_type,
            body=body,
            payload=payload,
        )


def revision_work_package_id(revision: RevisionRequest) -> str:
    return f"revision-{revision.id.hex[:12]}"
