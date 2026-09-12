from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.runs.state import RunStateService
from backend.app.orchestration.runs.status import RunStatus
from backend.app.orchestration.tasks.models import Task
from backend.app.orchestration.tasks.service import TaskStateService
from backend.app.orchestration.tasks.status import TaskStatus


class ApprovalWaitingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def mark_waiting(
        self, *, workspace_id: UUID, run_id: UUID | None, task_id: UUID | None,
    ) -> None:
        run = None
        task = None
        if run_id is not None:
            run = self._session.scalar(select(AgentRun).where(
                AgentRun.id == run_id, AgentRun.workspace_id == workspace_id,
            ))
            if run is None:
                raise ValueError("Approval run not found in workspace")
            if run.task_id != task_id:
                raise ValueError("Approval task does not match run")
        if task_id is not None:
            task = self._session.scalar(select(Task).where(
                Task.id == task_id, Task.workspace_id == workspace_id,
            ))
            if task is None:
                raise ValueError("Approval task not found in workspace")
        if run is not None:
            RunStateService().transition(run, RunStatus.WAITING_APPROVAL)
        if task is not None and task.status == TaskStatus.RUNNING.value:
            TaskStateService().transition(task, TaskStatus.WAITING_APPROVAL)
