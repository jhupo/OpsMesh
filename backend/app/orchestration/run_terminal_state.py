from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.memory.episodic import AgentEpisodicMemoryService
from backend.app.memory.working import AgentWorkingMemoryService
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import TaskStepStatus
from backend.app.workers.lease_lifecycle import mark_agent_run_worker_cancel_requested

AppendEvent = Callable[[AgentRun, str, str, dict[str, object] | None], RunEvent]
ReleaseRunReservations = Callable[[AgentRun, datetime], None]


@dataclass(slots=True)
class RunTerminalStateService:
    session: Session
    append_event: AppendEvent
    release_reservations: ReleaseRunReservations

    def mark_run_cancelled(self, run: AgentRun, *, completed_at: datetime) -> int:
        RunStateService().transition(
            run,
            RunStatus.CANCELLED,
            completed_at=completed_at,
            error={
                "code": "cancelled_by_user",
                "message": "Run was cancelled by a workspace user",
                "retryable": False,
            },
        )
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
                TaskStepStateService().transition(step, TaskStepStatus.CANCELLED)
        AgentEpisodicMemoryService(self.session).capture_run_cancelled(run)
        self._expire_working_memory(run)
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
        RunStateService().transition(
            run,
            RunStatus.FAILED,
            completed_at=datetime.now(UTC),
            error={
                "code": code,
                "message": message,
                "retryable": retryable,
            },
        )
        self.append_event(run, "run.recovered_failed", event_message, None)
        self.release_reservations(run, run.completed_at)
        if run.task_id is not None:
            task = self.session.get(Task, run.task_id)
            if task is not None and TaskStatus(task.status) not in TERMINAL_TASK_STATUSES:
                TaskStateService().transition(
                    task,
                    TaskStatus.FAILED,
                    completed_at=run.completed_at,
                )
                if run.task_step_id is not None:
                    step = self.session.get(TaskStep, run.task_step_id)
                    if step is not None and step.workspace_id == run.workspace_id:
                        TaskStepStateService().transition(step, TaskStepStatus.FAILED)
        AgentEpisodicMemoryService(self.session).capture_run_failed(
            run,
            run.error or {"code": code, "message": message, "retryable": retryable},
        )
        self._expire_working_memory(run)

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

    def _expire_working_memory(self, run: AgentRun) -> None:
        AgentWorkingMemoryService(self.session).expire_run(
            workspace_id=run.workspace_id,
            run_id=run.id,
        )
