from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.core.config import Settings
from backend.app.db.pagination import page_scalars
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskMessage

T = TypeVar("T")


class WorkspaceReadService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def list_task_messages(
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
        return self._page(statement.order_by(TaskMessage.sequence.asc()), page)

    def list_task_planning_attempts(
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
        return self._page(statement.order_by(TaskPlanningAttempt.attempt_number.desc()), page)

    def list_runs(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentRun], int]:
        statement = select(AgentRun).where(AgentRun.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        return self._page(statement.order_by(AgentRun.created_at.desc()), page)

    def list_run_events(
        self,
        workspace_id: UUID,
        agent_run_id: UUID,
        page: PageParams,
    ) -> tuple[list[RunEvent], int]:
        statement = (
            select(RunEvent)
            .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id == agent_run_id)
            .order_by(RunEvent.sequence.asc())
        )
        return self._page(statement, page)

    def list_audit_events(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[AuditEvent], int]:
        statement = (
            AuditService(self._session, self._settings)
            .apply_retention_to_statement(
                select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
            )
            .order_by(AuditEvent.created_at.desc())
        )
        return self._page(statement, page)

    def _require_task(self, workspace_id: UUID, task_id: UUID) -> None:
        exists = self._session.scalar(
            select(Task.id).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if exists is None:
            raise ValueError("Task not found")

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
