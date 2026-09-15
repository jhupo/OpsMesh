from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.utils import (
    dict_list,
    dict_or_empty,
    dict_or_none,
    int_or_zero,
    optional_string,
)
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage
from backend.app.domains.workspace.teams.execution.loop_support import TeamExecutionLoopRepository
from backend.app.domains.workspace.teams.operations.command_center import TeamCommandCenterService


def _result(
    task: Task,
    status: str,
    reason: str,
    *,
    final_output: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "task_id": task.id,
        "previous_status": task.status,
        "status": status,
        "reason": reason,
        "final_output": final_output,
    }


def _iteration_summary(
    *,
    command_center_actions: dict[str, object] | None,
    finalization: dict[str, object] | None,
    apply_command_center_actions: bool,
    finalize_ready_tasks: bool,
) -> dict[str, object]:
    return {
        "apply_command_center_actions": apply_command_center_actions,
        "finalize_ready_tasks": finalize_ready_tasks,
        "eligible_action_count": _int_from(command_center_actions, "eligible_action_count"),
        "applied_action_count": _int_from(command_center_actions, "applied_action_count"),
        "scheduled_run_count": _int_from(command_center_actions, "scheduled_run_count"),
        "scheduled_run_skip_reason": _string_from(
            command_center_actions,
            "scheduled_run_skip_reason",
        ),
        "scanned_task_count": _int_from(finalization, "scanned_task_count"),
        "finalized_task_count": _int_from(finalization, "finalized_task_count"),
        "skipped_task_count": _int_from(finalization, "skipped_task_count"),
    }


def _advanced(summary: dict[str, object]) -> bool:
    return any(
        _int(summary.get(key)) > 0
        for key in ("applied_action_count", "scheduled_run_count", "finalized_task_count")
    )


def _int_from(payload: dict[str, object] | None, key: str) -> int:
    if payload is None:
        return 0
    return _int(payload.get(key))


def _string_from(payload: dict[str, object] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int:
    return int_or_zero(value)


def _without_finalizable_review_actions(
    command_center: dict[str, object],
    finalization: dict[str, object],
) -> dict[str, object]:
    finalizable_task_ids = _finalizable_task_ids(finalization)
    if not finalizable_task_ids:
        return command_center

    action_plan = dict_list(command_center.get("action_plan"))
    filtered_actions: list[dict[str, object]] = []
    suppressed = 0
    changed = False
    for item in action_plan:
        action = item.get("action")
        task_ids = _object_list(item.get("task_ids"))
        if action != "request_manager_review" or not task_ids:
            filtered_actions.append(item)
            continue

        kept_task_ids = [
            task_id for task_id in task_ids if str(task_id) not in finalizable_task_ids
        ]
        changed = changed or kept_task_ids != task_ids
        if not kept_task_ids and not _object_list(item.get("task_step_ids")):
            suppressed += 1
            continue

        adjusted = {**item, "task_ids": kept_task_ids}
        count = adjusted.get("count")
        if isinstance(count, int):
            adjusted["count"] = min(count, len(kept_task_ids))
        filtered_actions.append(adjusted)

    if not changed:
        return command_center

    summary = dict_or_empty(command_center.get("summary"))
    summary["action_plan_count"] = len(filtered_actions)
    summary["action_plan_source_counts"] = _action_source_counts(filtered_actions)
    summary["suppressed_finalization_action_count"] = suppressed
    return {
        **command_center,
        "summary": summary,
        "action_plan": filtered_actions,
    }


def _finalizable_task_ids(finalization: dict[str, object]) -> set[str]:
    return {
        str(item["task_id"])
        for item in dict_list(finalization.get("results"))
        if item.get("status") in {"would_finalize", "finalized"} and item.get("task_id")
    }


def _action_source_counts(action_plan: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in action_plan:
        source = item.get("source")
        if isinstance(source, str) and source:
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _final_output_from_acceptance(message: TaskMessage) -> dict[str, object]:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary")
    return {
        "summary": summary if isinstance(summary, str) and summary else "Approved",
        "source": "pm_acceptance_decision",
        "acceptance_message_id": str(message.id),
        "decision": "approved",
    }


class ExecutionLoopStatusSummary(TypedDict):
    team_status: str | None
    delivery_health: dict[str, object] | None
    action_plan_count: int
    needs_attention_tasks: int
    finalizable_task_count: int
    scanned_task_count: int
    queue_truncated: bool


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
        from backend.app.domains.workspace.teams.execution.loop_finalization import (
            TeamExecutionFinalizationService,
        )

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
) -> ExecutionLoopStatusSummary:
    command_center_summary = dict_or_empty(command_center.get("summary"))
    return {
        "team_status": optional_string(command_center_summary.get("team_status")),
        "delivery_health": dict_or_none(command_center_summary.get("delivery_health")),
        "action_plan_count": _int_from(command_center_summary, "action_plan_count"),
        "needs_attention_tasks": _int_from(command_center_summary, "needs_attention_tasks"),
        "finalizable_task_count": len(_finalizable_task_ids(finalization)),
        "scanned_task_count": _int_from(finalization, "scanned_task_count"),
        "queue_truncated": bool(command_center_summary.get("queue_truncated")),
    }


def _status(summary: ExecutionLoopStatusSummary) -> str:
    if summary["action_plan_count"] > 0:
        return "needs_attention"
    if summary["finalizable_task_count"] > 0:
        return "ready_to_finalize"
    return "idle"
