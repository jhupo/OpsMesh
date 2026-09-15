from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage
from backend.app.domains.orchestration.workflows.planning.attempt_models import TaskPlanningAttempt


class TaskQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_messages(
        self,
        workspace_id: UUID,
        task_id: UUID,
        page: PageParams,
        message_type: str | None = None,
    ) -> tuple[list[TaskMessage], int]:
        self._require_task(workspace_id, task_id)
        statement = select(TaskMessage).where(
            TaskMessage.workspace_id == workspace_id,
            TaskMessage.task_id == task_id,
        )
        if message_type is not None:
            statement = statement.where(TaskMessage.message_type == message_type)
        return page_scalars(
            self._session,
            statement.order_by(TaskMessage.sequence.asc()),
            page,
        )

    def list_planning_attempts(
        self,
        workspace_id: UUID,
        task_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[TaskPlanningAttempt], int]:
        self._require_task(workspace_id, task_id)
        statement = select(TaskPlanningAttempt).where(
            TaskPlanningAttempt.workspace_id == workspace_id,
            TaskPlanningAttempt.task_id == task_id,
        )
        if status is not None:
            statement = statement.where(TaskPlanningAttempt.status == status)
        return page_scalars(
            self._session,
            statement.order_by(TaskPlanningAttempt.attempt_number.desc()),
            page,
        )

    def _require_task(self, workspace_id: UUID, task_id: UUID) -> None:
        task_id_in_workspace = self._session.scalar(
            select(Task.id).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task_id_in_workspace is None:
            raise ValueError("Task not found")
