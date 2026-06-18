from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.approvals.models import Approval
from backend.app.runs.models import AgentRun
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus


class ApprovalRunGateService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def fail_rejected_run(self, approval: Approval) -> None:
        completed_at = datetime.now(UTC)
        self._fail_run(approval, completed_at)
        self._fail_task(approval, completed_at)

    def _fail_run(self, approval: Approval, completed_at: datetime) -> None:
        if approval.agent_run_id is None:
            return
        run = self._session.get(AgentRun, approval.agent_run_id)
        if run is None:
            return
        RunStateService().transition(
            run,
            RunStatus.FAILED,
            completed_at=completed_at,
            error={"code": "approval_rejected", "message": "Approval was rejected"},
        )

    def _fail_task(self, approval: Approval, completed_at: datetime) -> None:
        if approval.task_id is None:
            return
        task = self._session.get(Task, approval.task_id)
        if task is None:
            return
        TaskStateService().transition(
            task,
            TaskStatus.FAILED,
            completed_at=completed_at,
        )
