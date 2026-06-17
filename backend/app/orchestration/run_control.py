from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_terminal_state import RunTerminalStateService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.reservation_release import RuntimeSpaceReservationReleaseService
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.workspaces.quotas import WorkspaceQuotaService

EnqueueRun = Callable[[AgentRun, UUID | None], bool]

@dataclass(frozen=True)
class StaleRunRecoverySummary:
    recovered_runs: int
    requeued_runs: int = 0
    failed_runs: int = 0


@dataclass(slots=True)
class RunControlService:
    session: Session
    enqueue_run: EnqueueRun

    def cancel_task(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
    ) -> Task | None:
        task = self.session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None
        if TaskStatus(task.status) == TaskStatus.CANCELLED:
            raise ValueError("Task is already cancelled")

        completed_at = datetime.now(UTC)
        TaskStateService().transition(task, TaskStatus.CANCELLED, completed_at=completed_at)

        active_runs = self.session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_RUNTIME.value,
                        RunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
        ).all()
        worker_cancel_requests = 0
        for run in active_runs:
            worker_cancel_requests += self.terminal_states().mark_run_cancelled(
                run,
                completed_at=completed_at,
            )

        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.cancelled",
            target_type="task",
            target_id=task.id,
            metadata={
                "title": task.title,
                "cancelled_runs": len(active_runs),
                "worker_cancel_requests": worker_cancel_requests,
            },
        )
        self.session.commit()
        self.session.refresh(task)
        return task

    def cancel_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
    ) -> AgentRun | None:
        run = self.session.scalar(
            select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
        )
        if run is None:
            return None

        completed_at = datetime.now(UTC)
        worker_cancel_requests = self.terminal_states().mark_run_cancelled(
            run,
            completed_at=completed_at,
        )
        if run.task_id is not None:
            task = self.session.get(Task, run.task_id)
            if task is not None and TaskStatus(task.status) not in TERMINAL_TASK_STATUSES:
                TaskStateService().transition(
                    task,
                    TaskStatus.CANCELLED,
                    completed_at=completed_at,
                )

        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="run.cancelled",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "task_id": str(run.task_id) if run.task_id is not None else None,
                "worker_cancel_requests": worker_cancel_requests,
            },
        )
        self.session.commit()
        self.session.refresh(run)
        return run

    def retry_failed_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
    ) -> AgentRun | None:
        failed_run = self.session.scalar(
            select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
        )
        if failed_run is None:
            return None
        if RunStatus(failed_run.status) != RunStatus.FAILED:
            raise ValueError("Only failed runs can be retried")

        task = (
            self.session.get(Task, failed_run.task_id)
            if failed_run.task_id is not None
            else None
        )
        if task is not None:
            TaskStateService().transition(task, TaskStatus.QUEUED)

        retry_run = AgentRun(
            workspace_id=failed_run.workspace_id,
            task_id=failed_run.task_id,
            task_step_id=failed_run.task_step_id,
            agent_profile_id=failed_run.agent_profile_id,
            runtime_id=failed_run.runtime_id,
            runtime_space_id=failed_run.runtime_space_id,
            status=RunStatus.QUEUED.value,
            input=failed_run.input,
            model=failed_run.model,
        )
        self.session.add(retry_run)
        self.session.flush()
        RunEventRecorder(self.session).append_event(
            retry_run,
            "run.retry_queued",
            f"Retry queued from failed run {failed_run.id}",
        )
        self.enqueue_run(retry_run, actor_user_id)
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="run.retried",
            target_type="agent_run",
            target_id=retry_run.id,
            metadata={
                "failed_run_id": str(failed_run.id),
                "task_id": str(failed_run.task_id) if failed_run.task_id is not None else None,
            },
        )
        self.session.commit()
        self.session.refresh(retry_run)
        return retry_run

    def recover_stale_running_runs(
        self,
        *,
        stale_after_seconds: int,
        limit: int = 100,
    ) -> StaleRunRecoverySummary:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        stale_runs = self.session.scalars(
            select(AgentRun)
            .where(
                AgentRun.status == RunStatus.RUNNING.value,
                AgentRun.started_at.is_not(None),
                AgentRun.started_at < cutoff,
            )
            .order_by(AgentRun.started_at.asc())
            .limit(limit)
        ).all()
        for run in stale_runs:
            self.terminal_states().mark_run_recovered_failed(run)
        self.session.commit()
        return StaleRunRecoverySummary(
            recovered_runs=len(stale_runs),
            failed_runs=len(stale_runs),
        )

    def recover_stale_worker_runs(
        self,
        *,
        stale_after_seconds: int,
        limit: int = 100,
        requested_by_user_id: UUID | None = None,
        reason: str | None = None,
    ) -> StaleRunRecoverySummary:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        candidates = self.session.scalars(
            select(AgentRun)
            .where(
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_RUNTIME.value,
                    ]
                )
            )
            .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
            .limit(limit * 3)
        ).all()
        stale_runs = [
            run
            for run in candidates
            if (anchor := stale_recovery_anchor(run)) is not None and anchor < cutoff
        ][:limit]

        requeued = 0
        failed = 0
        for run in stale_runs:
            if run.status == RunStatus.QUEUED.value:
                self.requeue_stale_run(
                    run,
                    requested_by_user_id=requested_by_user_id,
                    reason=reason or "worker_maintenance_stale_queued_run",
                )
                requeued += 1
                continue
            self.fail_recovered_run(
                run,
                code="stale_worker_run",
                message=stale_recovery_failure_message(run.status),
                retryable=True,
                event_message="Marked failed by worker maintenance recovery",
            )
            failed += 1
        self.session.commit()
        return StaleRunRecoverySummary(
            recovered_runs=requeued + failed,
            requeued_runs=requeued,
            failed_runs=failed,
        )

    def requeue_stale_run(
        self,
        run: AgentRun,
        *,
        requested_by_user_id: UUID | None,
        reason: str | None = None,
    ) -> bool:
        if RunStatus(run.status) != RunStatus.QUEUED:
            raise ValueError("Only queued runs can be requeued")
        run.error = None
        enqueued = self.enqueue_run(run, requested_by_user_id)
        RunEventRecorder(self.session).append_event(
            run,
            "run.requeued",
            "Requeued by stale run recovery control",
            {
                "reason": reason,
                "enqueued": enqueued,
            },
        )
        return enqueued

    def fail_recovered_run(
        self,
        run: AgentRun,
        *,
        code: str = "stale_worker_run",
        message: str = "Worker stopped reporting before the run completed",
        retryable: bool = True,
        event_message: str = "Marked failed after worker lease expired",
    ) -> None:
        self.terminal_states().mark_run_recovered_failed(
            run,
            code=code,
            message=message,
            retryable=retryable,
            event_message=event_message,
        )

    def release_reservations(self, run: AgentRun, *, released_at: datetime) -> None:
        RuntimeSpaceReservationReleaseService(self.session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )
        WorkspaceQuotaService(self.session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )

    def terminal_states(self) -> RunTerminalStateService:
        return RunTerminalStateService(
            session=self.session,
            append_event=RunEventRecorder(self.session).append_event,
            release_reservations=lambda run, released_at: self.release_reservations(
                run,
                released_at=released_at,
            ),
        )

def stale_recovery_anchor(run: AgentRun) -> datetime | None:
    if run.status == RunStatus.RUNNING.value:
        anchor = run.started_at or run.updated_at or run.created_at
    elif run.status == RunStatus.WAITING_RUNTIME.value:
        anchor = run.updated_at or run.started_at or run.created_at
    elif run.status == RunStatus.QUEUED.value:
        anchor = run.updated_at or run.created_at
    else:
        return None
    if anchor.tzinfo is None:
        return anchor.replace(tzinfo=UTC)
    return anchor


def stale_recovery_failure_message(status: str) -> str:
    if status == RunStatus.WAITING_RUNTIME.value:
        return "Runtime tool result did not arrive before the recovery window expired"
    return "Worker stopped reporting before the run completed"
