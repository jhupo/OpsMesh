from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.conditions import evaluate_task_step_condition
from backend.app.orchestration.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.orchestration.steps.dependencies import dependencies_satisfied, dependency_decision
from backend.app.runs.models import AgentRun
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
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
            .with_for_update(of=TaskStep, skip_locked=True)
            .execution_options(populate_existing=True)
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
            .with_for_update(of=TaskStep, skip_locked=True)
            .execution_options(populate_existing=True)
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
            .with_for_update(of=TaskStep, skip_locked=True)
            .execution_options(populate_existing=True)
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
                TaskStep.status.in_(
                    [
                        STEP_STATUS_QUEUED,
                        STEP_STATUS_RUNNING,
                        TaskStepStatus.BLOCKED.value,
                    ]
                ),
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
        pending = list(steps)
        while pending:
            step = pending.pop(0)
            if step.status != STEP_STATUS_QUEUED or self.step_has_active_run(step):
                continue
            dependency = dependency_decision(self.session, step)
            if dependency == "waiting":
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
            node_type = step.dependencies.get("node_type", "agent")
            if node_type in {"condition", "join", "start", "end"}:
                TaskStepStateService().transition(
                    step,
                    TaskStepStatus.RUNNING,
                )
                TaskStepStateService().transition(
                    step,
                    TaskStepStatus.COMPLETED,
                    result_summary="Workflow control node completed.",
                )
                self.session.flush()
                pending = [
                    candidate
                    for candidate in steps
                    if candidate.status == STEP_STATUS_QUEUED and candidate not in eligible
                ]
                continue
            decision = evaluate_task_step_condition(self.session, task, step)
            if dependency == "skip" or decision.state == "false":
                reason = (
                    "Required predecessor was skipped" if dependency == "skip" else decision.reason
                )
                self._skip_step(task, step, reason)
                self.session.flush()
                pending = [
                    candidate
                    for candidate in steps
                    if candidate.status == STEP_STATUS_QUEUED and candidate not in eligible
                ]
                continue
            if decision.state == "true":
                eligible.append(step)
        for task in task_cache.values():
            if task is not None:
                self._complete_empty_selection(task)
        return eligible

    def _complete_empty_selection(self, task: Task) -> None:
        if task.status in TERMINAL_TASK_STATUSES or self.task_has_open_team_work(task):
            return
        task_steps = self.session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        ).all()
        if not task_steps or any(
            step.status not in {TaskStepStatus.COMPLETED.value, TaskStepStatus.SKIPPED.value}
            for step in task_steps
        ):
            return
        control_types = {"condition", "join", "start", "end"}
        if any(
            step.status == TaskStepStatus.COMPLETED.value
            and (
                not isinstance(step.dependencies, dict)
                or step.dependencies.get("node_type", "agent") not in control_types
            )
            for step in task_steps
        ):
            return
        states = TaskStateService()
        if task.status == TaskStatus.DRAFT:
            states.transition(task, TaskStatus.QUEUED)
        states.transition(task, TaskStatus.RUNNING)
        states.transition(
            task,
            TaskStatus.COMPLETED,
            final_output={"summary": "No workflow branch selected.", "all_nodes_skipped": True},
        )
        TaskMessageAppendService(self.session).append_for_task(
            task,
            message_type="orchestration.empty_selection_completed",
            body="All workflow nodes were skipped; no agent run was needed.",
            payload={"all_nodes_skipped": True},
        )
        AuditService(self.session).record_system_action(
            workspace_id=task.workspace_id,
            action="orchestration.empty_selection_completed",
            target_type="task",
            target_id=task.id,
            metadata={"all_nodes_skipped": True},
        )

    def _skip_step(
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
            TaskStepStatus.SKIPPED,
            dependencies=dependencies,
            result_summary="Skipped by orchestration condition or dependency policy.",
        )
        TaskMessageAppendService(self.session).append_for_task(
            task,
            message_type="orchestration.step_skipped",
            body="An orchestration step was skipped by condition or dependency policy.",
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
