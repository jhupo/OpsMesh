from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.conditions import evaluate_task_step_condition
from backend.app.orchestration.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.orchestration.step_dependencies import dependencies_satisfied
from backend.app.runs.models import AgentRun
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import TaskStepStatus

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
        return self._eligible_with_conditions(queued_steps)

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
        return self._eligible_with_conditions(queued_steps)

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
        return self._eligible_with_conditions(queued_steps)

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
        return dependencies_satisfied(self.session, step)

    def _eligible_with_conditions(self, steps: Sequence[TaskStep]) -> list[TaskStep]:
        eligible: list[TaskStep] = []
        task_cache: dict[UUID, Task | None] = {}
        for step in steps:
            if not self.dependencies_satisfied(step) or self.step_has_active_run(step):
                continue
            task = task_cache.get(step.task_id)
            if step.task_id not in task_cache:
                task = self.session.scalar(
                    select(Task).where(
                        Task.workspace_id == step.workspace_id,
                        Task.id == step.task_id,
                    )
                )
                task_cache[step.task_id] = task
            if task is None:
                continue
            decision = evaluate_task_step_condition(self.session, task, step)
            if decision.state == "false":
                self._cancel_conditionally_skipped_step(task, step, decision.reason)
                continue
            if decision.state == "true":
                eligible.append(step)
        return eligible

    def _cancel_conditionally_skipped_step(
        self,
        task: Task,
        step: TaskStep,
        reason: str | None,
    ) -> None:
        dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
        dependencies["condition_result"] = "false"
        if reason:
            dependencies["condition_reason"] = reason[:500]
        TaskStepStateService().transition(
            step,
            TaskStepStatus.CANCELLED,
            dependencies=dependencies,
            result_summary="Skipped because its orchestration condition evaluated false.",
        )
        TaskMessageAppendService(self.session).append_for_task(
            task,
            message_type="orchestration.step_skipped",
            body="A user-authored orchestration step was skipped by its condition.",
            task_step_id=step.id,
            payload={
                "work_package_id": step.work_package_id,
                "condition_result": False,
                "reason": reason,
            },
        )
        AuditService(self.session).record_system_action(
            workspace_id=task.workspace_id,
            action="orchestration.step_skipped",
            target_type="task_step",
            target_id=step.id,
            metadata={
                "task_id": str(task.id),
                "work_package_id": step.work_package_id,
                "reason": reason,
            },
        )

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
