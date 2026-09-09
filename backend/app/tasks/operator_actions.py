from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.tasks.manager_review_requests import ManagerReviewRequestService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.operator_action_contracts import TaskOperatorActionResult
from backend.app.tasks.operator_action_recording import TaskOperatorActionRecorder
from backend.app.tasks.operator_dependencies import (
    completed_source_steps,
    dependency_step_ids,
    without_blocking_keys,
)
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import TaskStepStatus

TASK_OPERATOR_ACTIONS = {
    "requeue_blocked_steps",
    "reassign_step",
    "request_manager_review",
    "schedule_downstream_steps",
}
TERMINAL_STEP_STATUSES = {"completed", "cancelled"}
TERMINAL_TASK_STATUSES = {"completed", "cancelled"}
class TaskOperatorActionService:
    """Apply user/operator actions that turn task diagnostics into runnable work."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def apply_action(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        action: str,
        task_step_ids: list[UUID],
        agent_profile_id: UUID | None,
        instruction: str | None,
        reason: str | None,
        metadata: dict[str, object],
    ) -> dict[str, object] | None:
        if action not in TASK_OPERATOR_ACTIONS:
            raise ValueError("Unsupported task operator action")

        task = self._task(workspace_id, task_id)
        if task is None:
            return None
        if task.status in TERMINAL_TASK_STATUSES:
            raise ValueError("Terminal tasks cannot be modified by operator action")

        if action == "requeue_blocked_steps":
            result = self._requeue_blocked_steps(task, task_step_ids)
        elif action == "schedule_downstream_steps":
            result = self._schedule_downstream_steps(task, task_step_ids)
        elif action == "reassign_step":
            result = self._reassign_step(
                task,
                task_step_ids=task_step_ids,
                agent_profile_id=agent_profile_id,
            )
        else:
            result = ManagerReviewRequestService(self._session).request_manager_review(
                task,
                instruction=instruction,
                reason=reason,
            )

        recorder = TaskOperatorActionRecorder(self._session)
        message = recorder.append_message(
            task,
            action=action,
            result=result,
            instruction=instruction,
            reason=reason,
            metadata=metadata,
        )
        self._wake_task(task, result["changed_step_ids"] or result["created_step_ids"])
        recorder.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            task=task,
            action=action,
            task_step_ids=task_step_ids,
            agent_profile_id=agent_profile_id,
            result=result,
            message=message,
            reason=reason,
            metadata=metadata,
        )
        self._session.commit()
        self._session.refresh(task)
        return {
            "workspace_id": workspace_id,
            "task_id": task.id,
            "action": action,
            "status": "applied",
            "task_status": task.status,
            "changed_step_ids": result["changed_step_ids"],
            "created_step_ids": result["created_step_ids"],
            "message_id": message.id,
            "warnings": result["warnings"],
            "details": result["details"],
        }

    def _task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )

    def _requeue_blocked_steps(
        self,
        task: Task,
        task_step_ids: list[UUID],
    ) -> TaskOperatorActionResult:
        steps = self._target_steps(task, task_step_ids)
        if not steps:
            raise ValueError("No matching task steps found")

        changed_step_ids: list[UUID] = []
        warnings: list[str] = []
        for step in steps:
            if step.status != "blocked":
                warnings.append(f"step_not_blocked:{step.id}")
                continue
            TaskStepStateService().transition(
                step,
                TaskStepStatus.QUEUED,
                dependencies=without_blocking_keys(step.dependencies),
            )
            changed_step_ids.append(step.id)

        return {
            "changed_step_ids": changed_step_ids,
            "created_step_ids": [],
            "warnings": warnings,
            "details": {
                "requeued_steps": len(changed_step_ids),
                "requested_steps": len(steps),
            },
        }

    def _schedule_downstream_steps(
        self,
        task: Task,
        task_step_ids: list[UUID],
    ) -> TaskOperatorActionResult:
        steps = self._task_steps(task)
        step_by_id = {step.id: step for step in steps}
        source_steps = completed_source_steps(steps, task_step_ids)
        if task_step_ids and len(source_steps) != len(set(task_step_ids)):
            raise ValueError("Task step not found")
        if not source_steps:
            raise ValueError("No completed source task steps found")

        source_step_ids = {step.id for step in source_steps}
        changed_step_ids: list[UUID] = []
        scheduled_downstream_step_ids: list[UUID] = []
        cleared_blocking_step_ids: list[UUID] = []
        warnings: list[str] = []

        for step in steps:
            upstream_step_ids = dependency_step_ids(step.dependencies)
            if not upstream_step_ids or not source_step_ids.intersection(upstream_step_ids):
                continue
            missing_step_ids = [
                step_id for step_id in upstream_step_ids if step_id not in step_by_id
            ]
            incomplete_step_ids = [
                step_id
                for step_id in upstream_step_ids
                if step_id in step_by_id and step_by_id[step_id].status != "completed"
            ]
            if missing_step_ids or incomplete_step_ids:
                warnings.append(f"downstream_dependencies_incomplete:{step.id}")
                continue
            if step.status in TERMINAL_STEP_STATUSES:
                warnings.append(f"downstream_terminal:{step.id}")
                continue
            if step.status in {"running", "waiting_runtime", "waiting_approval"}:
                warnings.append(f"downstream_active:{step.id}")
                continue

            cleaned_dependencies = without_blocking_keys(step.dependencies)
            cleared_blocking = cleaned_dependencies != step.dependencies
            status_changed = step.status in {"blocked", "failed"}
            if cleared_blocking:
                step.dependencies = cleaned_dependencies
                cleared_blocking_step_ids.append(step.id)
            if status_changed:
                TaskStepStateService().transition(
                    step,
                    TaskStepStatus.QUEUED,
                    dependencies=cleaned_dependencies,
                )
            if step.status == "queued":
                scheduled_downstream_step_ids.append(step.id)
            if cleared_blocking or status_changed or step.status == "queued":
                changed_step_ids.append(step.id)

        if not scheduled_downstream_step_ids and not warnings:
            warnings.append("no_downstream_steps")

        return {
            "changed_step_ids": list(dict.fromkeys(changed_step_ids)),
            "created_step_ids": [],
            "warnings": warnings,
            "details": {
                "source_step_ids": [str(step_id) for step_id in source_step_ids],
                "scheduled_downstream_step_ids": [
                    str(step_id) for step_id in dict.fromkeys(scheduled_downstream_step_ids)
                ],
                "cleared_blocking_step_ids": [
                    str(step_id) for step_id in dict.fromkeys(cleared_blocking_step_ids)
                ],
            },
        }

    def _reassign_step(
        self,
        task: Task,
        *,
        task_step_ids: list[UUID],
        agent_profile_id: UUID | None,
    ) -> TaskOperatorActionResult:
        if len(task_step_ids) != 1:
            raise ValueError("reassign_step requires exactly one task_step_id")
        if agent_profile_id is None:
            raise ValueError("reassign_step requires agent_profile_id")
        agent = self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == task.workspace_id,
                AgentProfile.id == agent_profile_id,
                AgentProfile.status == "active",
            )
        )
        if agent is None:
            raise ValueError("Agent profile not found")

        step = self._target_steps(task, task_step_ids)[0]
        if step.status in TERMINAL_STEP_STATUSES:
            raise ValueError("Terminal task steps cannot be reassigned")

        previous_agent_profile_id = step.assigned_agent_profile_id
        step.assigned_agent_profile_id = agent_profile_id
        if step.status in {"blocked", "failed"}:
            TaskStepStateService().transition(
                step,
                TaskStepStatus.QUEUED,
                dependencies=without_blocking_keys(step.dependencies),
            )

        return {
            "changed_step_ids": [step.id],
            "created_step_ids": [],
            "warnings": [],
            "details": {
                "task_step_id": str(step.id),
                "previous_agent_profile_id": (
                    str(previous_agent_profile_id) if previous_agent_profile_id else None
                ),
                "agent_profile_id": str(agent_profile_id),
                "agent_role": agent.role,
            },
        }

    def _target_steps(self, task: Task, task_step_ids: list[UUID]) -> list[TaskStep]:
        statement = select(TaskStep).where(
            TaskStep.workspace_id == task.workspace_id,
            TaskStep.task_id == task.id,
        )
        if task_step_ids:
            statement = statement.where(TaskStep.id.in_(task_step_ids))
        else:
            statement = statement.where(TaskStep.status == "blocked")
        steps = self._session.scalars(
            statement.order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
        ).all()
        if task_step_ids and len(steps) != len(set(task_step_ids)):
            raise ValueError("Task step not found")
        return list(steps)

    def _task_steps(self, task: Task) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(
                    TaskStep.workspace_id == task.workspace_id,
                    TaskStep.task_id == task.id,
                )
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _wake_task(self, task: Task, affected_step_ids: list[UUID]) -> None:
        if not affected_step_ids:
            return
        if task.status == TaskStatus.BLOCKED.value:
            TaskStateService().transition(task, TaskStatus.RUNNING)
        elif task.status == TaskStatus.FAILED.value:
            TaskStateService().transition(task, TaskStatus.QUEUED)
