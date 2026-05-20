from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.approvals.service import ApprovalService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.planning.project_plans import (
    ProjectPlanningService,
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.tasks.models import Task, TaskMessage
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus


class TaskPlanningAttemptService:
    def __init__(
        self,
        session: Session,
        planner: ProjectPlanningService | None = None,
    ) -> None:
        self._session = session
        self._planner = planner or ProjectPlanningService()

    def ensure_initial_plan(self, task: Task) -> dict[str, object] | None:
        if task.project_plan is not None:
            return task.project_plan
        TaskStateService().transition(task, TaskStatus.PLANNING)
        attempt = self._create_attempt(task, retry_count=self._next_retry_count(task))
        try:
            plan = self._planner.create_initial_plan(task)
            if plan is not None:
                validate_project_plan(plan, task.team_snapshot)
        except ProjectPlanValidationError as exc:
            self._mark_failed(task, attempt, [str(exc)])
            return None
        except Exception as exc:
            self._mark_failed(task, attempt, [f"{exc.__class__.__name__}: {exc}"])
            return None

        now = datetime.now(UTC)
        attempt.status = "completed"
        attempt.output_snapshot = plan
        attempt.completed_at = now
        task.project_plan = plan
        self._append_message(
            task,
            message_type="planning.completed",
            body="Project plan generated.",
            payload={
                "attempt_id": str(attempt.id),
                "work_package_count": _work_package_count(plan),
            },
        )
        self._session.flush([attempt, task])
        return plan

    def _create_attempt(self, task: Task, *, retry_count: int) -> TaskPlanningAttempt:
        attempt = TaskPlanningAttempt(
            workspace_id=task.workspace_id,
            task_id=task.id,
            planner_agent_profile_id=_planner_agent_profile_id(task),
            attempt_number=self._next_attempt_number(task.workspace_id, task.id),
            status="running",
            strategy="deterministic_team_snapshot_v1",
            input_snapshot={
                "title": task.title,
                "description": task.description,
                "domain_type": task.domain_type,
                "input": task.input,
                "team_snapshot": task.team_snapshot,
            },
            retry_count=retry_count,
            created_at=datetime.now(UTC),
        )
        self._session.add(attempt)
        self._session.flush([attempt])
        return attempt

    def _mark_failed(
        self,
        task: Task,
        attempt: TaskPlanningAttempt,
        validation_errors: list[str],
    ) -> None:
        now = datetime.now(UTC)
        attempt.status = "failed"
        attempt.validation_errors = validation_errors
        attempt.completed_at = now
        task.generic_state = {
            **(task.generic_state if isinstance(task.generic_state, dict) else {}),
            "planning": {
                "status": "failed",
                "latest_attempt_id": str(attempt.id),
                "validation_errors": validation_errors,
                "failed_at": now.isoformat(),
            },
        }
        TaskStateService().transition(task, TaskStatus.BLOCKED)
        approval = ApprovalService(self._session).create_approval(
            workspace_id=task.workspace_id,
            task_id=task.id,
            agent_run_id=None,
            requested_by_agent_profile_id=attempt.planner_agent_profile_id,
            approval_type="task.plan_review",
            risk_level="medium",
            payload={
                "reason": "planning_failed",
                "attempt_id": str(attempt.id),
                "validation_errors": validation_errors,
                "suggested_actions": [
                    "Review the team roster and requested work packages.",
                    "Retry planning after correcting invalid roles, skills, or dependencies.",
                ],
            },
        )
        self._append_message(
            task,
            message_type="planning.failed",
            body="Project planning failed and needs review.",
            payload={
                "attempt_id": str(attempt.id),
                "approval_id": str(approval.id),
                "validation_errors": validation_errors,
            },
        )
        self._session.flush([attempt, task])

    def _next_attempt_number(self, workspace_id: UUID, task_id: UUID) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskPlanningAttempt.attempt_number), 0)).where(
                TaskPlanningAttempt.workspace_id == workspace_id,
                TaskPlanningAttempt.task_id == task_id,
            )
        )
        return int(current or 0) + 1

    def _next_retry_count(self, task: Task) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskPlanningAttempt.retry_count), -1)).where(
                TaskPlanningAttempt.workspace_id == task.workspace_id,
                TaskPlanningAttempt.task_id == task.id,
            )
        )
        return int(current if current is not None else -1) + 1

    def _append_message(
        self,
        task: Task,
        *,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> TaskMessage:
        sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
                    TaskMessage.workspace_id == task.workspace_id,
                    TaskMessage.task_id == task.id,
                )
            )
            or 0
        ) + 1
        message = TaskMessage(
            workspace_id=task.workspace_id,
            task_id=task.id,
            message_type=message_type,
            sequence=sequence,
            body=body,
            payload=payload,
        )
        self._session.add(message)
        self._session.flush([message])
        return message


def _planner_agent_profile_id(task: Task) -> UUID | None:
    snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else {}
    team = snapshot.get("team") if isinstance(snapshot.get("team"), dict) else {}
    raw_id = team.get("manager_agent_profile_id") if isinstance(team, dict) else None
    if raw_id is None:
        return None
    try:
        return UUID(str(raw_id))
    except ValueError:
        return None


def _work_package_count(plan: dict[str, object] | None) -> int:
    if not isinstance(plan, dict):
        return 0
    packages = plan.get("work_packages")
    return len(packages) if isinstance(packages, list) else 0
