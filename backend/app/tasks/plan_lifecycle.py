from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.agent_plan import is_agent_planning_step
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.queue.redis_queue import RedisQueue


@dataclass(frozen=True, slots=True)
class TaskPlanRetryCommand:
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = False


@dataclass(frozen=True, slots=True)
class TaskPlanRegenerateCommand:
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = True


class TaskPlanLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def retry_task_plan(
        self,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        command: TaskPlanRetryCommand,
        *,
        enqueue_run: bool = False,
        queue: RedisQueue | None = None,
    ) -> Task | None:
        task = self._get_task(workspace_id, task_id)
        if task is None:
            return None
        if task.agent_team_id is None:
            raise ValueError("Task is not team-backed")
        self._require_initial_retry(task)
        if command.input is not None:
            task.input = command.input
        self._refresh_team_snapshot(task, task.agent_team_id, refresh=command.refresh_team_snapshot)
        task.project_plan = None
        if task.status in {"blocked", "failed"}:
            TaskStateService().reset_to_draft(task)
        TaskPlanningAttemptService(self._session).ensure_initial_plan(
            task,
            transition_to_planning=task.status in {"draft", "queued", "blocked", "failed"},
        )
        run = None
        if task.project_plan is not None:
            run = RunOrchestrationService(self._session).create_queued_run_for_task(task)
            if enqueue_run and run is not None:
                RunOrchestrationService(self._session, queue=queue).enqueue_run(
                    run,
                    actor_user_id,
                )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.plan_retried",
            target_type="task",
            target_id=task.id,
            metadata={
                "refresh_team_snapshot": command.refresh_team_snapshot,
                "input_replaced": command.input is not None,
                "planned": task.project_plan is not None,
                "run_id": str(run.id) if run is not None else None,
            },
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def regenerate_task_plan(
        self,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        command: TaskPlanRegenerateCommand,
        *,
        enqueue_run: bool = False,
        queue: RedisQueue | None = None,
    ) -> Task | None:
        task = self._get_task(workspace_id, task_id)
        if task is None:
            return None
        if task.agent_team_id is None:
            raise ValueError("Task is not team-backed")

        completed_work_package_ids = self._completed_work_package_ids(workspace_id, task.id)
        if command.input is not None:
            task.input = command.input
        self._refresh_team_snapshot(task, task.agent_team_id, refresh=command.refresh_team_snapshot)

        previous_plan = task.project_plan
        task.project_plan = None
        if task.status in {"blocked", "failed"}:
            TaskStateService().reset_to_draft(task)
        TaskPlanningAttemptService(self._session).ensure_initial_plan(task)
        if task.project_plan is not None:
            task.project_plan = {
                **task.project_plan,
                "regeneration": {
                    "mode": "future_only",
                    "previous_plan_id": previous_plan.get("plan_id")
                    if isinstance(previous_plan, dict)
                    else None,
                    "preserved_completed_work_package_ids": sorted(completed_work_package_ids),
                    "regenerated_by_user_id": str(actor_user_id),
                },
            }
            self._sync_latest_planning_attempt_output(workspace_id, task.id, task.project_plan)

        run = None
        if task.project_plan is not None and not completed_work_package_ids:
            run = RunOrchestrationService(self._session).create_queued_run_for_task(task)
            if enqueue_run and run is not None:
                RunOrchestrationService(self._session, queue=queue).enqueue_run(
                    run,
                    actor_user_id,
                )

        self._append_plan_regenerated_message(
            task,
            completed_work_package_ids,
            run_id=run.id if run is not None else None,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.plan_regenerated",
            target_type="task",
            target_id=task.id,
            metadata={
                "refresh_team_snapshot": command.refresh_team_snapshot,
                "input_replaced": command.input is not None,
                "preserved_completed_work_package_ids": sorted(completed_work_package_ids),
                "run_id": str(run.id) if run is not None else None,
            },
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def _get_task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task)
            .where(Task.workspace_id == workspace_id, Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _require_initial_retry(self, task: Task) -> None:
        if task.status not in {"blocked", "failed"}:
            raise ValueError("Only failed or blocked initial planning can be retried")
        active = self._session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.not_in(
                    [
                        RunStatus.COMPLETED.value,
                        RunStatus.FAILED.value,
                        RunStatus.CANCELLED.value,
                    ]
                ),
            )
            .limit(1)
        )
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        ).all()
        if active is not None or any(
            not is_agent_planning_step(step) or step.status not in {"failed", "cancelled"}
            for step in steps
        ):
            raise ValueError("Existing task work requires explicit replanning")

    def _refresh_team_snapshot(
        self,
        task: Task,
        team_id: UUID,
        *,
        refresh: bool,
    ) -> None:
        if refresh or task.team_snapshot is None:
            task.team_snapshot = build_team_snapshot(
                self._session,
                workspace_id=task.workspace_id,
                team_id=team_id,
            )

    def _completed_work_package_ids(self, workspace_id: UUID, task_id: UUID) -> set[str]:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status == "completed",
                TaskStep.work_package_id.is_not(None),
            )
        ).all()
        return {str(step.work_package_id) for step in steps if step.work_package_id}

    def _sync_latest_planning_attempt_output(
        self,
        workspace_id: UUID,
        task_id: UUID,
        project_plan: dict[str, object],
    ) -> None:
        attempt = self._session.scalar(
            select(TaskPlanningAttempt)
            .where(
                TaskPlanningAttempt.workspace_id == workspace_id,
                TaskPlanningAttempt.task_id == task_id,
                TaskPlanningAttempt.status == "completed",
            )
            .order_by(TaskPlanningAttempt.attempt_number.desc())
        )
        if attempt is not None:
            attempt.output_snapshot = project_plan
            self._session.flush([attempt])

    def _append_plan_regenerated_message(
        self,
        task: Task,
        completed_work_package_ids: set[str],
        *,
        run_id: UUID | None,
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type="planning.regenerated",
            body="Project plan regenerated for future work.",
            payload={
                "mode": "future_only",
                "planned": task.project_plan is not None,
                "preserved_completed_work_package_ids": sorted(completed_work_package_ids),
                "run_id": str(run_id) if run_id is not None else None,
            },
        )
