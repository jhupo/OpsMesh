from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.orchestration.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.status import TaskStatus

STEP_STATUS_QUEUED = "queued"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_COMPLETED = "completed"


@dataclass(slots=True)
class RunEligibilityService:
    session: Session

    def next_eligible_steps(self, task_id: UUID, workspace_id: UUID) -> list[TaskStep]:
        queued_steps = self.session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status == STEP_STATUS_QUEUED,
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        return [
            step
            for step in queued_steps
            if self.dependencies_satisfied(step) and not self.step_has_active_run(step)
        ]

    def workspace_eligible_steps(self, workspace_id: UUID) -> list[TaskStep]:
        queued_steps = self.session.scalars(
            select(TaskStep)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == STEP_STATUS_QUEUED,
                Task.status.in_(
                    [
                        TaskStatus.QUEUED.value,
                        TaskStatus.RUNNING.value,
                        TaskStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
            .order_by(Task.priority.desc(), TaskStep.order_index.asc())
        ).all()
        return [step for step in queued_steps if self.dependencies_satisfied(step)]

    def team_eligible_steps(self, workspace_id: UUID, team_id: UUID) -> list[TaskStep]:
        queued_steps = self.session.scalars(
            select(TaskStep)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.agent_team_id == team_id,
                TaskStep.status == STEP_STATUS_QUEUED,
                Task.status.in_(
                    [
                        TaskStatus.QUEUED.value,
                        TaskStatus.RUNNING.value,
                        TaskStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
            .order_by(Task.priority.desc(), TaskStep.order_index.asc())
        ).all()
        return [step for step in queued_steps if self.dependencies_satisfied(step)]

    def step_has_active_run(self, step: TaskStep) -> bool:
        active_count = self.session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.workspace_id == step.workspace_id,
                AgentRun.task_step_id == step.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
        )
        return int(active_count or 0) > 0

    def task_has_open_team_work(self, task: Task) -> bool:
        incomplete_steps = self.session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status.in_([STEP_STATUS_QUEUED, STEP_STATUS_RUNNING]),
            )
        )
        if int(incomplete_steps or 0) > 0:
            return True

        active_runs = self.session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
        )
        return int(active_runs or 0) > 0

    def dependencies_satisfied(self, step: TaskStep) -> bool:
        dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
        raw_step_ids = dependencies.get("after_step_ids", [])
        if not isinstance(raw_step_ids, list) or not raw_step_ids:
            return True

        dependency_ids: list[UUID] = []
        for raw_step_id in raw_step_ids:
            try:
                dependency_ids.append(UUID(str(raw_step_id)))
            except ValueError:
                return False

        incomplete_count = self.session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == step.workspace_id,
                TaskStep.id.in_(dependency_ids),
                TaskStep.status != STEP_STATUS_COMPLETED,
            )
        )
        return int(incomplete_count or 0) == 0

    def completed_step_summaries(self, task_id: UUID, *, before: int) -> list[str]:
        completed_steps = self.session.scalars(
            select(TaskStep)
            .where(
                TaskStep.task_id == task_id,
                TaskStep.status == STEP_STATUS_COMPLETED,
                TaskStep.order_index < before,
                TaskStep.result_summary.is_not(None),
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        return [
            f"- {step.title}: {step.result_summary}"
            for step in completed_steps
            if step.result_summary
        ]
