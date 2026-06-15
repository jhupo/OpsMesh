from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus, require_run_transition
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.workers.lease_lifecycle import mark_agent_run_worker_cancel_requested

STEP_STATUS_CANCELLED = "cancelled"
STEP_STATUS_FAILED = "failed"

AppendEvent = Callable[[AgentRun, str, str, dict[str, object] | None], RunEvent]
ReleaseRunReservations = Callable[[AgentRun, datetime], None]


@dataclass(slots=True)
class RunTerminalStateService:
    session: Session
    append_event: AppendEvent
    release_reservations: ReleaseRunReservations

    def mark_run_cancelled(self, run: AgentRun, *, completed_at: datetime) -> int:
        require_run_transition(RunStatus(run.status), RunStatus.CANCELLED)
        run.status = RunStatus.CANCELLED.value
        run.error = {
            "code": "cancelled_by_user",
            "message": "Run was cancelled by a workspace user",
            "retryable": False,
        }
        run.completed_at = completed_at
        worker_cancel_requests = self.record_worker_cancel_requested(
            run,
            requested_at=completed_at,
        )
        self.append_event(
            run,
            "run.cancelled",
            "Run was cancelled by a workspace user",
            {"worker_cancel_requests": worker_cancel_requests},
        )
        self.release_reservations(run, completed_at)
        if run.task_step_id is not None:
            step = self.session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_CANCELLED
        return worker_cancel_requests

    def mark_run_recovered_failed(
        self,
        run: AgentRun,
        *,
        code: str = "stale_worker_run",
        message: str = "Worker stopped reporting before the run completed",
        retryable: bool = True,
        event_message: str = "Marked failed after worker lease expired",
    ) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.FAILED)
        run.status = RunStatus.FAILED.value
        run.error = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        run.completed_at = datetime.now(UTC)
        self.append_event(run, "run.recovered_failed", event_message, None)
        self.release_reservations(run, run.completed_at)

        if run.task_id is None:
            return
        task = self.session.get(Task, run.task_id)
        if task is None or TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
            return
        TaskStateService().transition(task, TaskStatus.FAILED, completed_at=run.completed_at)
        if run.task_step_id is not None:
            step = self.session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_FAILED

    def record_worker_cancel_requested(
        self,
        run: AgentRun,
        *,
        requested_at: datetime,
    ) -> int:
        return mark_agent_run_worker_cancel_requested(
            self.session,
            workspace_id=run.workspace_id,
            run_id=run.id,
            requested_at=requested_at,
        )
