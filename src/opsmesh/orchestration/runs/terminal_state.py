from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.approvals.lifecycle import AgentToolApprovalLifecycleService
from opsmesh.orchestration.planning.attempts import TaskPlanningAttemptService
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.runs.state import RunStatus, transition_run
from opsmesh.orchestration.runs.statuses import ACTIVE_RUN_STATUS_VALUES
from opsmesh.orchestration.runs.subworkflow_models import SubworkflowInvocation
from opsmesh.orchestration.runs.temporal import run_execution_reference
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.orchestration.tasks.state import (
    TERMINAL_TASK_STATUSES,
    TaskStatus,
    transition_task,
)
from opsmesh.orchestration.tasks.steps import TaskStepStatus, transition_task_step
from opsmesh.resources.memory.episodic import AgentEpisodicMemoryService
from opsmesh.resources.memory.working import AgentWorkingMemoryService
from opsmesh.runtime.temporal.outbox import stage_cancellation, stage_signal

AppendEvent = Callable[[AgentRun, str, str, dict[str, object] | None], RunEvent]
ReleaseRunReservations = Callable[[AgentRun, datetime], None]


class RunCancellationScope(StrEnum):
    EXECUTION = "execution"
    TASK_TREE = "task_tree"


@dataclass(slots=True)
class RunTerminalStateService:
    session: Session
    append_event: AppendEvent
    release_reservations: ReleaseRunReservations

    def mark_run_cancelled(
        self,
        run: AgentRun,
        *,
        completed_at: datetime,
        error_code: str = "cancelled_by_user",
        error_message: str = "Run was cancelled by a workspace user",
        scope: RunCancellationScope = RunCancellationScope.TASK_TREE,
        actor_user_id: UUID | None = None,
    ) -> None:
        if run.status not in ACTIVE_RUN_STATUS_VALUES:
            return
        TaskPlanningAttemptService(self.session).finish_unsuccessful_run(
            run, code="planner_cancelled", cancelled=True
        )
        transition_run(
            run,
            RunStatus.CANCELLED,
            completed_at=completed_at,
            error={
                "code": error_code,
                "message": error_message,
                "retryable": False,
            },
        )
        self.append_event(
            run,
            "run.cancelled",
            error_message,
            {},
        )
        self.release_reservations(run, completed_at)
        if scope == RunCancellationScope.TASK_TREE and run.task_step_id is not None:
            step = self.session.scalar(
                select(TaskStep).where(
                    TaskStep.workspace_id == run.workspace_id,
                    TaskStep.task_id == run.task_id,
                    TaskStep.id == run.task_step_id,
                )
            )
            if step is not None:
                transition_task_step(step, TaskStepStatus.CANCELLED)
        AgentEpisodicMemoryService(self.session).capture_run_cancelled(run)
        self._expire_working_memory(run)
        AgentToolApprovalLifecycleService(self.session).cancel_for_run(
            workspace_id=run.workspace_id, run_id=run.id, actor_user_id=actor_user_id
        )
        self._cancel_subworkflow_children(run, completed_at, actor_user_id)
        if scope == RunCancellationScope.TASK_TREE and run.task_id is not None:
            task = self.session.scalar(
                select(Task).where(Task.workspace_id == run.workspace_id, Task.id == run.task_id)
            )
            if task is not None and TaskStatus(task.status) not in TERMINAL_TASK_STATUSES:
                transition_task(task, TaskStatus.CANCELLED, completed_at=completed_at)
            self.cancel_task_steps(workspace_id=run.workspace_id, task_id=run.task_id)
            self._cancel_task_siblings(run, completed_at, actor_user_id)
            self._close_parent_invocation(run, completed_at)

    def _cancel_task_siblings(
        self, run: AgentRun, completed_at: datetime, actor_user_id: UUID | None
    ) -> None:
        siblings = self.session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == run.workspace_id,
                AgentRun.task_id == run.task_id,
                AgentRun.id != run.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
            .order_by(AgentRun.id)
            .with_for_update()
        ).all()
        for sibling in siblings:
            if sibling.status not in ACTIVE_RUN_STATUS_VALUES:
                continue
            self.mark_run_cancelled(
                sibling,
                completed_at=completed_at,
                error_code="task_cancelled",
                error_message="Owning task was cancelled",
                actor_user_id=actor_user_id,
            )
            stage_cancellation(
                self.session,
                dispatch_id=uuid4(),
                workspace_id=run.workspace_id,
                workflow_id=run_execution_reference(self.session, sibling).workflow_id,
            )

    def _cancel_subworkflow_children(
        self, run: AgentRun, completed_at: datetime, actor_user_id: UUID | None
    ) -> None:
        invocations = self.session.scalars(
            select(SubworkflowInvocation)
            .where(
                SubworkflowInvocation.workspace_id == run.workspace_id,
                SubworkflowInvocation.parent_run_id == run.id,
                SubworkflowInvocation.status.in_(("pending", "running")),
            )
            .with_for_update()
        ).all()
        for invocation in invocations:
            invocation.status = "cancelled"
            invocation.completed_at = completed_at
            invocation.error_payload = {
                "code": "parent_cancelled",
                "message": "Parent execution was cancelled",
                "retryable": False,
            }
            self.session.flush([invocation])
            task = self.session.scalar(
                select(Task)
                .where(Task.workspace_id == run.workspace_id, Task.id == invocation.child_task_id)
                .with_for_update()
            )
            if task is not None and TaskStatus(task.status) not in TERMINAL_TASK_STATUSES:
                transition_task(task, TaskStatus.CANCELLED, completed_at=completed_at)
            children = self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == run.workspace_id,
                    AgentRun.task_id == invocation.child_task_id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
                )
                .order_by(AgentRun.id)
                .with_for_update()
            ).all()
            for child in children:
                if child.status not in ACTIVE_RUN_STATUS_VALUES:
                    continue
                self.mark_run_cancelled(
                    child,
                    completed_at=completed_at,
                    error_code="parent_cancelled",
                    error_message="Parent execution was cancelled",
                    actor_user_id=actor_user_id,
                )
                stage_cancellation(
                    self.session,
                    dispatch_id=uuid4(),
                    workspace_id=run.workspace_id,
                    workflow_id=run_execution_reference(self.session, child).workflow_id,
                )
            self.cancel_task_steps(workspace_id=run.workspace_id, task_id=invocation.child_task_id)

    def cancel_task_steps(self, *, workspace_id: UUID, task_id: UUID) -> None:
        for step in self.session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status.in_(("queued", "running", "blocked")),
            )
        ):
            transition_task_step(step, TaskStepStatus.CANCELLED)

    def _close_parent_invocation(self, run: AgentRun, completed_at: datetime) -> None:
        invocation = self.session.scalar(
            select(SubworkflowInvocation)
            .where(
                SubworkflowInvocation.workspace_id == run.workspace_id,
                SubworkflowInvocation.child_task_id == run.task_id,
                SubworkflowInvocation.status.in_(("pending", "running")),
            )
            .with_for_update()
        )
        if invocation is None:
            return
        invocation.status = "cancelled"
        invocation.completed_at = completed_at
        invocation.error_payload = run.error
        parent = self.session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == run.workspace_id,
                AgentRun.id == invocation.parent_run_id,
            )
            .with_for_update()
        )
        self.session.flush([invocation])
        if parent is not None and parent.status == RunStatus.WAITING_SUBWORKFLOW.value:
            self.mark_run_cancelled(
                parent,
                completed_at=completed_at,
                error_code="subworkflow_cancelled",
                error_message="Subworkflow child execution was cancelled",
            )
            stage_signal(
                self.session,
                dispatch_id=uuid4(),
                workspace_id=parent.workspace_id,
                workflow_id=run_execution_reference(self.session, parent).workflow_id,
                signal_name="child_completed",
                resource_id=invocation.id,
            )

    def _expire_working_memory(self, run: AgentRun) -> None:
        AgentWorkingMemoryService(self.session).expire_run(
            workspace_id=run.workspace_id,
            run_id=run.id,
        )
