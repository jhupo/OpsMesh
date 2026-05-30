from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.operator_actions import TaskOperatorActionService
from backend.app.teams.models import AgentTeam

TERMINAL_TASK_STATUSES = {"completed", "cancelled", "canceled"}
TEAM_OPERATOR_ACTIONS = {
    "request_manager_review",
    "requeue_blocked_steps",
    "schedule_downstream_steps",
}


class TeamOperatorActionService:
    """Apply supported operator actions across a reusable team task set."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def apply_action(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        action: str,
        task_ids: list[UUID],
        task_step_ids: list[UUID],
        max_tasks: int,
        instruction: str | None,
        reason: str | None,
        metadata: dict[str, object],
    ) -> dict[str, object] | None:
        if action not in TEAM_OPERATOR_ACTIONS:
            raise ValueError("Unsupported team operator action")
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return None

        step_ids_by_task, step_warnings = self._step_ids_by_task(
            workspace_id=workspace_id,
            team_id=team_id,
            task_step_ids=task_step_ids,
        )
        effective_task_ids = task_ids or sorted(step_ids_by_task, key=str)
        selected_tasks = self._tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            task_ids=effective_task_ids,
            max_tasks=max_tasks,
        )
        selected_by_id = {task.id: task for task in selected_tasks}
        results = [
            _missing_task_result(task_id)
            for task_id in _dedupe_uuids(effective_task_ids)
            if task_id not in selected_by_id
        ]
        task_action = TaskOperatorActionService(self._session)
        for task in selected_tasks:
            if task.status in TERMINAL_TASK_STATUSES:
                results.append(_skipped_task_result(task.id, "terminal_task"))
                continue
            try:
                response = task_action.apply_action(
                    workspace_id=workspace_id,
                    task_id=task.id,
                    actor_user_id=actor_user_id,
                    action=action,
                    task_step_ids=step_ids_by_task.get(task.id, []),
                    agent_profile_id=None,
                    instruction=instruction,
                    reason=reason or "team_operator_action",
                    metadata=_team_action_metadata(team_id, metadata),
                )
            except ValueError as exc:
                results.append(_skipped_task_result(task.id, str(exc)))
                continue
            if response is None:
                results.append(_missing_task_result(task.id))
                continue
            results.append(
                {
                    "task_id": task.id,
                    "status": "applied",
                    "message": None,
                    "changed_step_ids": response["changed_step_ids"],
                    "created_step_ids": response["created_step_ids"],
                    "message_id": response["message_id"],
                }
            )

        applied_count = sum(1 for result in results if result["status"] == "applied")
        skipped_count = len(results) - applied_count
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "action": action,
            "status": "applied" if applied_count else "noop",
            "requested_task_count": (
                len(effective_task_ids) if effective_task_ids else len(selected_tasks)
            ),
            "applied_count": applied_count,
            "skipped_count": skipped_count,
            "warnings": step_warnings,
            "results": results,
        }

    def _step_ids_by_task(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        task_step_ids: list[UUID],
    ) -> tuple[dict[UUID, list[UUID]], list[str]]:
        if not task_step_ids:
            return {}, []
        requested_step_ids = _dedupe_uuids(task_step_ids)
        steps = list(
            self._session.scalars(
                select(TaskStep)
                .join(Task, Task.id == TaskStep.task_id)
                .where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.id.in_(requested_step_ids),
                    Task.agent_team_id == team_id,
                )
            )
        )
        found_step_ids = {step.id for step in steps}
        grouped: dict[UUID, list[UUID]] = {}
        for step in steps:
            grouped.setdefault(step.task_id, []).append(step.id)
        warnings = [
            f"task_step_not_found_or_not_in_team:{step_id}"
            for step_id in requested_step_ids
            if step_id not in found_step_ids
        ]
        grouped_step_ids = {
            task_id: sorted(step_ids, key=str) for task_id, step_ids in grouped.items()
        }
        return grouped_step_ids, warnings

    def _tasks(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        task_ids: list[UUID],
        max_tasks: int,
    ) -> list[Task]:
        statement = select(Task).where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id == team_id,
        )
        if task_ids:
            statement = statement.where(Task.id.in_(_dedupe_uuids(task_ids)))
        else:
            statement = statement.where(~Task.status.in_(TERMINAL_TASK_STATUSES)).limit(max_tasks)
        return list(
            self._session.scalars(
                statement.order_by(Task.priority.desc(), Task.updated_at.desc(), Task.id.asc())
            )
        )


def _team_action_metadata(team_id: UUID, metadata: dict[str, object]) -> dict[str, object]:
    return {
        **metadata,
        "source": "team_operator_action",
        "team_id": str(team_id),
    }


def _missing_task_result(task_id: UUID) -> dict[str, object]:
    return {
        "task_id": task_id,
        "status": "skipped",
        "message": "task_not_found_or_not_in_team",
        "changed_step_ids": [],
        "created_step_ids": [],
        "message_id": None,
    }


def _skipped_task_result(task_id: UUID, message: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "status": "skipped",
        "message": message,
        "changed_step_ids": [],
        "created_step_ids": [],
        "message_id": None,
    }


def _dedupe_uuids(values: list[UUID]) -> list[UUID]:
    return list(dict.fromkeys(values))
