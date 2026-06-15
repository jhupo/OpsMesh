from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.execution_loop_finalization import TeamExecutionFinalizationService
from backend.app.teams.execution_loop_payloads import (
    _finalizable_task_ids,
    _int_from,
    _without_finalizable_review_actions,
)
from backend.app.teams.execution_loop_repository import TeamExecutionLoopRepository


class TeamExecutionLoopStatusService:
    """Build the execution loop status view without mutating team state."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamExecutionLoopRepository(session)

    def get_status(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        max_finalize_tasks: int = 50,
    ) -> dict[str, object] | None:
        if not self._repo.team_exists(workspace_id=workspace_id, team_id=team_id):
            return None

        command_center = TeamCommandCenterService(self._session).get_command_center(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
        )
        if command_center is None:
            return None

        finalization = TeamExecutionFinalizationService(self._session).finalize_ready_tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=None,
            dry_run=True,
            max_tasks=max_finalize_tasks,
        )
        if finalization is None:
            return None
        command_center = _without_finalizable_review_actions(command_center, finalization)
        summary = _status_summary(command_center, finalization)

        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "status": _status(summary),
            "summary": summary,
            "command_center": command_center,
            "finalization": finalization,
        }


def _status_summary(
    command_center: dict[str, object],
    finalization: dict[str, object],
) -> dict[str, object]:
    command_center_summary = (
        command_center.get("summary")
        if isinstance(command_center.get("summary"), dict)
        else {}
    )
    return {
        "team_status": command_center_summary.get("team_status"),
        "delivery_health": command_center_summary.get("delivery_health"),
        "action_plan_count": _int_from(command_center_summary, "action_plan_count"),
        "needs_attention_tasks": _int_from(command_center_summary, "needs_attention_tasks"),
        "finalizable_task_count": len(_finalizable_task_ids(finalization)),
        "scanned_task_count": _int_from(finalization, "scanned_task_count"),
        "queue_truncated": bool(command_center_summary.get("queue_truncated")),
    }


def _status(summary: dict[str, object]) -> str:
    if summary["action_plan_count"] > 0:
        return "needs_attention"
    if summary["finalizable_task_count"] > 0:
        return "ready_to_finalize"
    return "idle"
