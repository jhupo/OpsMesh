from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.tasks import TaskControlActionRequest
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runs.models import AgentRun
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import TaskStepStatus
from backend.app.workers.lease_lifecycle import mark_agent_run_worker_cancel_requested
from backend.app.workers.queue.redis_queue import RedisQueue

PAUSABLE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}
PAUSABLE_STEP_STATUSES = {"queued", "running", "blocked"}
TASK_PAUSED_REASON = "task_paused"


class TaskControlExecutionService:
    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def cancel_active_runs(self, task: Task, *, now: datetime) -> tuple[int, int]:
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(PAUSABLE_RUN_STATUSES),
            )
        ).all()
        worker_cancel_requests = 0
        for run in runs:
            RunStateService().transition(
                run,
                RunStatus.CANCELLED,
                completed_at=now,
                error={"code": TASK_PAUSED_REASON, "message": "Task paused by owner control"},
            )
            worker_cancel_requests += mark_agent_run_worker_cancel_requested(
                self._session,
                workspace_id=run.workspace_id,
                run_id=run.id,
                requested_at=now,
            )
        return len(runs), worker_cancel_requests

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
            TaskStepStateService().transition(
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
            TaskStepStateService().transition(
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
        orchestrator = RunOrchestrationService(self._session, queue=self._queue)
        if task.agent_team_id is not None:
            return orchestrator.schedule_team_steps(
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
                requested_by_user_id=actor_user_id,
            )
        run = AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            status=RunStatus.QUEUED.value,
            input={
                "task_id": str(task.id),
                "title": task.title,
                "source": "task_control_resume",
            },
        )
        self._session.add(run)
        self._session.flush()
        orchestrator.enqueue_run(run, actor_user_id)
        return [run]
