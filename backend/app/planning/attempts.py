from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.approvals.service import ApprovalService
from backend.app.planning.agent_plan import bootstrap_plan, is_agent_planning_step, planning_mode
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.planning.ownership import require_automatic_plan_ownership
from backend.app.planning.project_plans import (
    ProjectPlanningService,
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.runs.models import AgentRun
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
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

    def ensure_initial_plan(
        self,
        task: Task,
        *,
        transition_to_planning: bool = True,
    ) -> dict[str, object] | None:
        self._session.scalar(
            select(Task)
            .where(Task.workspace_id == task.workspace_id, Task.id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task.project_plan is not None:
            return task.project_plan
        require_automatic_plan_ownership(task)
        if transition_to_planning:
            TaskStateService().transition(task, TaskStatus.PLANNING)
        attempt = self._create_attempt(task, retry_count=self._next_retry_count(task))
        plan: dict[str, object] | None
        try:
            if planning_mode(task) == "agent":
                plan = bootstrap_plan(task, attempt.id)
                attempt.strategy = "agent_sdk"
                attempt.status = "queued"
                attempt.planner_agent_profile_id = UUID(str(plan["planner_agent_profile_id"]))
                task.project_plan = plan
                self._append_message(
                    task,
                    message_type="planning.requested",
                    body="Agent planning queued.",
                    payload={"attempt_id": str(attempt.id), "strategy": "agent_sdk"},
                )
                self._session.flush([attempt, task])
                return plan
            plan = self._planner.create_initial_plan(task)
            if plan is not None:
                validate_project_plan(plan, task.team_snapshot)
        except ProjectPlanValidationError as exc:
            self._mark_failed(task, attempt, [str(exc)])
            return None
        except Exception as exc:
            self._mark_failed(task, attempt, [f"planning_failed:{exc.__class__.__name__}"])
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
                "strategy": "deterministic_team_snapshot_v1",
                "explicit_fallback": True,
            },
        )
        self._session.flush([attempt, task])
        return plan

    def reject_agent_plan(self, task: Task, attempt: TaskPlanningAttempt, *, code: str) -> None:
        self._mark_failed(task, attempt, [code])

    def finish_unsuccessful_run(self, run: AgentRun, *, code: str, cancelled: bool = False) -> bool:
        step = self._session.scalar(
            select(TaskStep).where(
                TaskStep.workspace_id == run.workspace_id,
                TaskStep.task_id == run.task_id,
                TaskStep.id == run.task_step_id,
            )
        )
        if not is_agent_planning_step(step):
            return False
        assert step is not None
        task = self._session.scalar(
            select(Task)
            .where(
                Task.workspace_id == run.workspace_id,
                Task.id == run.task_id,
            )
            .with_for_update()
        )
        attempt = self._session.scalar(
            select(TaskPlanningAttempt)
            .where(
                TaskPlanningAttempt.workspace_id == run.workspace_id,
                TaskPlanningAttempt.task_id == run.task_id,
                TaskPlanningAttempt.id == UUID(str(step.review_policy["attempt_id"])),
            )
            .with_for_update()
        )
        if task is None or attempt is None:
            raise ValueError("Planning run has no scoped attempt")
        if (task.project_plan or {}).get("plan_id") != str(attempt.id):
            return False
        if attempt.status in {"queued", "running"}:
            if cancelled:
                attempt.status = "cancelled"
                attempt.completed_at = datetime.now(UTC)
                self._append_message(
                    task,
                    message_type="planning.cancelled",
                    body="Agent planning cancelled.",
                    payload={"attempt_id": str(attempt.id)},
                )
            else:
                self._mark_failed(task, attempt, [code])
        return attempt.status == "failed"

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
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type=message_type,
            body=body,
            payload=payload,
        )


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
