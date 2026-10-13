from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.approvals.lifecycle import AgentToolApprovalLifecycleService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.events import RunEventRecorder
from opsmesh.orchestration.runs.models import AUTHORIZATION_SNAPSHOT_VERSION, AgentRun
from opsmesh.orchestration.runs.queries import authorization_snapshot_for_run
from opsmesh.orchestration.runs.resources import (
    release_run_reservations,
)
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.runs.statuses import (
    ACTIVE_RUN_STATUS_VALUES,
)
from opsmesh.orchestration.runs.temporal import run_execution_reference
from opsmesh.orchestration.runs.terminal_state import RunTerminalStateService
from opsmesh.orchestration.runs.tree_lock import lock_run_execution_tree, lock_task_execution_tree
from opsmesh.orchestration.tasks.models import Task
from opsmesh.orchestration.tasks.state import (
    TERMINAL_TASK_STATUSES,
    TaskStatus,
    transition_task,
)
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.runtime.temporal.outbox import stage_cancellation, stage_execution
from opsmesh.shared.errors import ConflictError
from opsmesh.workspaces.projects.snapshots.service import RunProjectSnapshotService


@dataclass(slots=True)
class RunControlService:
    session: Session

    def cancel_task(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        commit: bool = True,
    ) -> Task | None:
        lock_task_execution_tree(self.session, workspace_id, task_id)
        task = self.session.scalar(
            select(Task)
            .where(Task.workspace_id == workspace_id, Task.id == task_id)
            .with_for_update()
        )
        if task is None:
            return None
        if TaskStatus(task.status) == TaskStatus.CANCELLED:
            raise ValueError("Task is already cancelled")

        completed_at = datetime.now(UTC)
        transition_task(task, TaskStatus.CANCELLED, completed_at=completed_at)

        active_runs = self.session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
        ).all()
        cancelled_approvals = (
            self.session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(
                    Approval.workspace_id == workspace_id,
                    Approval.agent_run_id.in_([run.id for run in active_runs]),
                    Approval.status == "pending",
                )
            )
            or 0
        )
        for run in active_runs:
            if run.status not in ACTIVE_RUN_STATUS_VALUES:
                continue
            self.terminal_states().mark_run_cancelled(
                run,
                completed_at=completed_at,
                actor_user_id=actor_user_id,
            )
            self._stage_run_cancellation(run)
        self.terminal_states().cancel_task_steps(workspace_id=workspace_id, task_id=task.id)

        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.cancelled",
            target_type="task",
            target_id=task.id,
            metadata={
                "title": task.title,
                "cancelled_runs": len(active_runs),
                "temporal_cancellation_count": len(active_runs),
                "cancelled_approvals": cancelled_approvals,
            },
        )
        if commit:
            self.session.commit()
        self.session.refresh(task)
        return task

    def cancel_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
        commit: bool = True,
    ) -> AgentRun | None:
        lock_run_execution_tree(self.session, workspace_id, run_id)
        run = self.session.scalar(
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            return None

        completed_at = datetime.now(UTC)
        was_active = run.status in ACTIVE_RUN_STATUS_VALUES
        if not was_active:
            if commit:
                self.session.commit()
            return run
        cancelled_approvals = AgentToolApprovalLifecycleService(self.session).cancel_for_run(
            workspace_id=workspace_id,
            run_id=run.id,
            actor_user_id=actor_user_id,
        )
        self.terminal_states().mark_run_cancelled(
            run,
            completed_at=completed_at,
            actor_user_id=actor_user_id,
        )
        self._stage_run_cancellation(run)
        if run.task_id is not None:
            task = self.session.get(Task, run.task_id)
            if task is not None and TaskStatus(task.status) not in TERMINAL_TASK_STATUSES:
                transition_task(
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
                "temporal_cancellation_count": 1 if was_active else 0,
                "cancelled_approvals": cancelled_approvals,
            },
        )
        if commit:
            self.session.commit()
            self.session.refresh(run)
        return run

    def retry_failed_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
        commit: bool = True,
    ) -> AgentRun | None:
        lock_run_execution_tree(self.session, workspace_id, run_id)
        failed_run = self.session.scalar(
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if failed_run is None:
            return None
        if RunStatus(failed_run.status) != RunStatus.FAILED:
            raise ValueError("Only failed runs can be retried")
        execution_lane = (
            (AgentRun.task_id == failed_run.task_id)
            & (AgentRun.task_step_id == failed_run.task_step_id)
            if failed_run.task_id is not None
            else AgentRun.id == failed_run.id
        )
        if (
            self.session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    execution_lane,
                    AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES)
                    | (AgentRun.started_at.is_not(None) & AgentRun.execution_released_at.is_(None)),
                )
                .limit(1)
            )
            is not None
        ):
            raise ConflictError("Previous execution has not released its session")
        require_execution_admission(self.session)
        snapshot = authorization_snapshot_for_run(failed_run)
        if snapshot and snapshot.get("version") != AUTHORIZATION_SNAPSHOT_VERSION:
            raise ValueError(
                "Historical authorization snapshot cannot be retried; submit a new task"
            )

        task = (
            self.session.get(Task, failed_run.task_id) if failed_run.task_id is not None else None
        )
        if task is not None:
            transition_task(task, TaskStatus.QUEUED)

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
        RunProjectSnapshotService(self.session).clone_for_retry(
            source_run=failed_run,
            retry_run=retry_run,
        )
        RunEventRecorder(self.session).append_event(
            retry_run,
            "run.retry_queued",
            f"Retry queued from failed run {failed_run.id}",
        )
        self._stage_run_execution(retry_run, actor_user_id)
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
        if commit:
            self.session.commit()
            self.session.refresh(retry_run)
        return retry_run

    def release_reservations(self, run: AgentRun, *, released_at: datetime) -> None:
        release_run_reservations(self.session, run, released_at=released_at)

    def _stage_run_execution(
        self,
        run: AgentRun,
        requested_by_user_id: UUID | None,
    ) -> None:
        stage_execution(
            self.session,
            run_execution_reference(self.session, run, requested_by_user_id),
            dispatch_id=uuid4(),
        )

    def _stage_run_cancellation(self, run: AgentRun) -> None:
        stage_cancellation(
            self.session,
            dispatch_id=uuid4(),
            workspace_id=run.workspace_id,
            workflow_id=run_execution_reference(self.session, run).workflow_id,
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
