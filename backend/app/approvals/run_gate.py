from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.approvals.models import Approval
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.runs.models import AgentRun
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.reservation_release import RuntimeSpaceReservationReleaseService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import FINAL_STEP_STATUSES, TaskStepStatus
from backend.app.workspaces.quotas import WorkspaceQuotaService


class ApprovalRunGateService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def fail_rejected_run(self, approval: Approval) -> None:
        self.fail_run(
            approval,
            code="approval_rejected",
            message="Approval was rejected",
        )

    def fail_run(self, approval: Approval, *, code: str, message: str) -> None:
        completed_at = datetime.now(UTC)
        self._fail_run(approval, completed_at, code=code, message=message)
        self._fail_task(approval, completed_at)

    def _fail_run(
        self,
        approval: Approval,
        completed_at: datetime,
        *,
        code: str,
        message: str,
    ) -> None:
        if approval.agent_run_id is None:
            return
        run = self._session.get(AgentRun, approval.agent_run_id)
        if run is None:
            return
        if RunStatus(run.status) in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return
        RunStateService().transition(
            run,
            RunStatus.FAILED,
            completed_at=completed_at,
            error={"code": code, "message": message},
        )
        RunEventRecorder(self._session).append_event(
            run,
            f"run.{code}",
            message,
            {"approval_id": str(approval.id)},
        )
        RuntimeSpaceReservationReleaseService(
            self._session
        ).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=completed_at,
        )
        WorkspaceQuotaService(self._session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=completed_at,
        )
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if (
                step is not None
                and step.workspace_id == run.workspace_id
                and TaskStepStatus(step.status) not in FINAL_STEP_STATUSES
            ):
                TaskStepStateService().transition(step, TaskStepStatus.FAILED)

    def _fail_task(self, approval: Approval, completed_at: datetime) -> None:
        if approval.task_id is None:
            return
        task = self._session.get(Task, approval.task_id)
        if task is None or TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
            return
        TaskStateService().transition(
            task,
            TaskStatus.FAILED,
            completed_at=completed_at,
        )
