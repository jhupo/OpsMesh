from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.teams.execution_overview import TeamExecutionOverviewService


class TeamCommandCenterService:
    """Compose team execution diagnostics into a single operator-facing view."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_command_center(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
    ) -> dict[str, object] | None:
        overview = TeamExecutionOverviewService(self._session).get_overview(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
        )
        if overview is None:
            return None

        handoff_queue = TaskExecutionDiagnosticsService(self._session).list_handoff_queue(
            workspace_id=workspace_id,
            limit=queue_limit,
            offset=0,
            team_id=team_id,
            include_terminal=False,
        )
        manager_queue = TaskManagerDiagnosticsService(self._session).list_manager_queue(
            workspace_id=workspace_id,
            limit=queue_limit,
            offset=0,
            team_id=team_id,
            include_healthy=False,
        )
        action_plan = _merged_action_plan(
            overview=overview,
            handoff_queue=handoff_queue,
            manager_queue=manager_queue,
        )
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "summary": _summary(
                overview=overview,
                handoff_queue=handoff_queue,
                manager_queue=manager_queue,
                action_plan=action_plan,
                queue_limit=queue_limit,
            ),
            "overview": overview,
            "queues": {
                "handoff": handoff_queue,
                "manager": manager_queue,
            },
            "action_plan": action_plan,
        }


def _summary(
    *,
    overview: dict[str, object],
    handoff_queue: dict[str, object],
    manager_queue: dict[str, object],
    action_plan: list[dict[str, object]],
    queue_limit: int,
) -> dict[str, object]:
    overview_summary = _dict(overview.get("summary"))
    source_counts: dict[str, int] = {}
    for item in action_plan:
        source = item.get("source")
        if isinstance(source, str):
            source_counts[source] = source_counts.get(source, 0) + 1

    return {
        "team_status": _dict(overview.get("team")).get("status"),
        "delivery_health": overview_summary.get("delivery_health"),
        "total_tasks": _int(overview_summary.get("total_tasks")),
        "needs_attention_tasks": _int(overview_summary.get("needs_attention_tasks")),
        "blocked_tasks": _int(overview_summary.get("blocked_tasks")),
        "handoff_queue_total": _int(handoff_queue.get("total")),
        "manager_queue_total": _int(manager_queue.get("total")),
        "queue_limit": queue_limit,
        "queue_truncated": (
            _int(handoff_queue.get("total")) > queue_limit
            or _int(manager_queue.get("total")) > queue_limit
        ),
        "action_plan_count": len(action_plan),
        "action_plan_source_counts": dict(sorted(source_counts.items())),
    }


def _merged_action_plan(
    *,
    overview: dict[str, object],
    handoff_queue: dict[str, object],
    manager_queue: dict[str, object],
) -> list[dict[str, object]]:
    return [
        *_source_action_plan(
            source="execution_overview",
            items=_list(_dict(overview.get("summary")).get("intervention_plan")),
        ),
        *_source_action_plan(
            source="handoff_queue",
            items=_list(_dict(handoff_queue.get("summary")).get("team_operator_action_plan")),
        ),
        *_source_action_plan(
            source="manager_queue",
            items=_list(_dict(manager_queue.get("summary")).get("team_operator_action_plan")),
        ),
    ]


def _source_action_plan(
    *,
    source: str,
    items: list[object],
) -> list[dict[str, object]]:
    action_plan: list[dict[str, object]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        action_plan.append(
            {
                **item,
                "source": source,
                "source_index": index,
            }
        )
    return action_plan


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
