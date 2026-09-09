from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.operator_action_contracts import TaskOperatorActionResult
from backend.app.tasks.operator_dependencies import manager_agent_id


class ManagerReviewRequestService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def request_manager_review(
        self,
        task: Task,
        *,
        instruction: str | None,
        reason: str | None,
    ) -> TaskOperatorActionResult:
        manager_id = manager_agent_id(task)
        if manager_id is None:
            raise ValueError("Task manager not found")
        manager = self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == task.workspace_id,
                AgentProfile.id == manager_id,
                AgentProfile.status == "active",
            )
        )
        if manager is None:
            raise ValueError("Task manager not found")

        next_order = self._next_step_order(task.workspace_id, task.id)
        cycle = self._next_manager_review_cycle(task.workspace_id, task.id)
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            assigned_agent_profile_id=manager.id,
            runtime_space_id=task.runtime_space_id,
            work_package_id=f"manager-summary-operator-{cycle}",
            required_role="project_manager",
            required_skills=["project_management", "review"],
            expected_artifacts=["manager_review"],
            acceptance_criteria=["Manager review is recorded with a decision."],
            review_policy={"mode": "operator_requested_review"},
            title="Operator requested manager review",
            description=instruction or "Review current task progress and decide next action.",
            status="queued",
            order_index=next_order,
            dependencies={
                "operator_action": {
                    "action": "request_manager_review",
                    "reason": reason,
                    "requested_at": datetime.now(UTC).isoformat(),
                }
            },
        )
        self._session.add(step)
        self._session.flush([step])
        return {
            "changed_step_ids": [],
            "created_step_ids": [step.id],
            "warnings": [],
            "details": {
                "manager_agent_profile_id": str(manager.id),
                "created_work_package_id": step.work_package_id,
                "order_index": step.order_index,
            },
        }

    def _next_step_order(self, workspace_id: UUID, task_id: UUID) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskStep.order_index), 0)).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
            )
        )
        return int(current or 0) + 1

    def _next_manager_review_cycle(self, workspace_id: UUID, task_id: UUID) -> int:
        existing = self._session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.work_package_id.like("manager-summary-operator-%"),
            )
        )
        return int(existing or 0) + 1
