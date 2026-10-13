from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.approvals.lifecycle import AgentToolApprovalLifecycleService
from opsmesh.orchestration.runs.control import RunControlService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.runs.temporal import run_execution_reference
from opsmesh.orchestration.runs.terminal_state import RunCancellationScope
from opsmesh.orchestration.tasks.contracts import TaskControlActionRequest
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.orchestration.tasks.steps import TaskStepStatus, transition_task_step
from opsmesh.runtime.temporal.outbox import stage_cancellation

PAUSABLE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_APPROVAL.value,
    RunStatus.WAITING_SUBWORKFLOW.value,
}
PAUSABLE_STEP_STATUSES = {"queued", "running", "blocked"}
TASK_PAUSED_REASON = "task_paused"


class TaskControlExecutionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def cancel_active_runs(
        self, task: Task, *, now: datetime, actor_user_id: UUID
    ) -> tuple[int, int]:
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(PAUSABLE_RUN_STATUSES),
            )
        ).all()
        temporal_cancellations = 0
        for run in runs:
            AgentToolApprovalLifecycleService(self._session).cancel_for_run(
                workspace_id=task.workspace_id,
                run_id=run.id,
                actor_user_id=actor_user_id,
            )
            RunControlService(self._session).terminal_states().mark_run_cancelled(
                run,
                completed_at=now,
                error_code=TASK_PAUSED_REASON,
                error_message="Task paused by owner control",
                scope=RunCancellationScope.EXECUTION,
                actor_user_id=actor_user_id,
            )
            stage_cancellation(
                self._session,
                dispatch_id=uuid4(),
                workspace_id=run.workspace_id,
                workflow_id=run_execution_reference(self._session, run).workflow_id,
            )
            temporal_cancellations += 1
        return len(runs), temporal_cancellations

    def block_schedulable_steps(
        self,
        task: Task,
        *,
        request: TaskControlActionRequest,
        now: datetime,
    ) -> int:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status.in_(PAUSABLE_STEP_STATUSES),
            )
        ).all()
        for step in steps:
            dependencies = dict(step.dependencies or {})
            dependencies.update(
                {
                    "task_control_paused": True,
                    "blocked_reason": TASK_PAUSED_REASON,
                    "blocked_at": now.isoformat(),
                    "blocked_by": "task_control",
                    "pause_reason": request.reason,
                }
            )
            transition_task_step(
                step,
                TaskStepStatus.BLOCKED,
                dependencies=dependencies,
            )
        return len(steps)

    def unblock_paused_steps(self, task: Task) -> int:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status == "blocked",
            )
        ).all()
        changed = 0
        for step in steps:
            dependencies = dict(step.dependencies or {})
            if dependencies.get("task_control_paused") is not True:
                continue
            for key in (
                "task_control_paused",
                "blocked_reason",
                "blocked_at",
                "blocked_by",
                "pause_reason",
            ):
                dependencies.pop(key, None)
            transition_task_step(
                step,
                TaskStepStatus.QUEUED,
                dependencies=dependencies,
            )
            changed += 1
        return changed

    def schedule_task_work(
        self,
        task: Task,
        actor_user_id: UUID,
        *,
        enqueue: bool,
    ) -> list[AgentRun]:
        if not enqueue:
            return []
        orchestrator = RunOrchestrationService(self._session)
        if task.agent_team_id is not None:
            return orchestrator.schedule_team_steps(
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
                requested_by_user_id=actor_user_id,
            )
        run = orchestrator.create_queued_run_for_task(task)
        if run is None:
            return []
        orchestrator.request_run(run, actor_user_id)
        return [run]
