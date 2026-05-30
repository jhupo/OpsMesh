from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runs.models import AgentRun
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.teams.execution_overview import TeamExecutionOverviewService
from backend.app.teams.operator_actions import TEAM_OPERATOR_ACTIONS, TeamOperatorActionService
from backend.app.workers.queue import RedisQueue

COMMAND_CENTER_ACTION_SOURCES = {
    "execution_overview",
    "handoff_queue",
    "manager_queue",
}


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

    def apply_action_plan(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        dry_run: bool = True,
        sources: list[str] | None = None,
        actions: list[str] | None = None,
        max_actions: int = 5,
        max_tasks_per_action: int = 100,
        enqueue_runs: bool = False,
        queue: RedisQueue | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        command_center = self.get_command_center(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
        )
        if command_center is None:
            return None

        action_plan = _list(command_center.get("action_plan"))
        grouped, skipped = _group_applicable_actions(
            action_plan=action_plan,
            sources=sources,
            actions=actions,
            max_actions=max_actions,
        )
        results = (
            [
                {
                    "action": item["action"],
                    "status": "would_apply",
                    "sources": item["sources"],
                    "task_ids": item["task_ids"],
                    "task_step_ids": item["task_step_ids"],
                    "candidate_count": item["candidate_count"],
                    "response": None,
                }
                for item in grouped
            ]
            if dry_run
            else self._apply_grouped_actions(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                grouped=grouped,
                max_tasks_per_action=max_tasks_per_action,
                reason=reason,
                metadata=metadata or {},
            )
        )
        scheduled_runs = (
            RunOrchestrationService(self._session, queue=queue).schedule_team_steps(
                workspace_id=workspace_id,
                team_id=team_id,
                requested_by_user_id=actor_user_id,
            )
            if enqueue_runs and not dry_run
            else []
        )
        applied_action_count = sum(1 for item in results if item["status"] == "applied")
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "applied" if applied_action_count else "noop",
            "requested_action_count": len(action_plan),
            "eligible_action_count": len(grouped),
            "applied_action_count": applied_action_count,
            "skipped_action_count": len(skipped),
            "summary": command_center["summary"],
            "results": results,
            "skipped": skipped,
            "scheduled_run_count": len(scheduled_runs),
            "scheduled_runs": [_scheduled_run_payload(run) for run in scheduled_runs],
        }

    def _apply_grouped_actions(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        grouped: list[dict[str, object]],
        max_tasks_per_action: int,
        reason: str | None,
        metadata: dict[str, object],
    ) -> list[dict[str, object]]:
        operator_actions = TeamOperatorActionService(self._session)
        results: list[dict[str, object]] = []
        for item in grouped:
            response = operator_actions.apply_action(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                action=str(item["action"]),
                task_ids=_uuid_list(item.get("task_ids")),
                task_step_ids=_uuid_list(item.get("task_step_ids")),
                max_tasks=max_tasks_per_action,
                instruction=None,
                reason=reason or "team_command_center",
                metadata={
                    **metadata,
                    "command_center_sources": item["sources"],
                    "command_center_candidate_count": item["candidate_count"],
                },
            )
            results.append(
                {
                    "action": item["action"],
                    "status": response["status"] if response is not None else "noop",
                    "sources": item["sources"],
                    "task_ids": item["task_ids"],
                    "task_step_ids": item["task_step_ids"],
                    "candidate_count": item["candidate_count"],
                    "response": response,
                }
            )
        return results


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


def _group_applicable_actions(
    *,
    action_plan: list[object],
    sources: list[str] | None,
    actions: list[str] | None,
    max_actions: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    allowed_sources = set(sources or COMMAND_CENTER_ACTION_SOURCES)
    allowed_actions = set(actions or TEAM_OPERATOR_ACTIONS)
    grouped: dict[str, dict[str, object]] = {}
    skipped: list[dict[str, object]] = []

    for index, item in enumerate(action_plan):
        if not isinstance(item, dict):
            skipped.append(_skip(index, "invalid_action_plan_item", item))
            continue
        source = item.get("source")
        action = item.get("action")
        if not isinstance(source, str) or source not in COMMAND_CENTER_ACTION_SOURCES:
            skipped.append(_skip(index, "unsupported_source", item))
            continue
        if source not in allowed_sources:
            continue
        if not isinstance(action, str) or action not in TEAM_OPERATOR_ACTIONS:
            skipped.append(_skip(index, "unsupported_action", item))
            continue
        if action not in allowed_actions:
            continue
        if item.get("automation") != "team_operator_action":
            skipped.append(_skip(index, "unsupported_automation", item))
            continue

        group = grouped.setdefault(
            action,
            {
                "action": action,
                "sources": [],
                "task_ids": [],
                "task_step_ids": [],
                "candidate_count": 0,
                "max_priority": 0,
            },
        )
        _append_strings(group, "sources", source)
        _extend_uuids(group, "task_ids", _uuid_list(item.get("task_ids")))
        _extend_uuids(group, "task_step_ids", _uuid_list(item.get("task_step_ids")))
        group["candidate_count"] = int(group["candidate_count"]) + 1
        group["max_priority"] = max(int(group["max_priority"]), _int(item.get("priority")))

    ordered = sorted(
        grouped.values(),
        key=lambda item: (-int(item["max_priority"]), str(item["action"])),
    )
    selected = ordered[:max_actions]
    for item in ordered[max_actions:]:
        skipped.append(
            {
                "source_index": None,
                "source": None,
                "action": item["action"],
                "reason": "max_actions_exceeded",
            }
        )
    return selected, skipped


def _skip(index: int, reason: str, item: dict[str, object] | object) -> dict[str, object]:
    payload = item if isinstance(item, dict) else {}
    return {
        "source_index": index,
        "source": payload.get("source"),
        "action": payload.get("action"),
        "reason": reason,
    }


def _scheduled_run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "agent_run_id": run.id,
        "task_id": run.task_id,
        "task_step_id": run.task_step_id,
        "agent_profile_id": run.agent_profile_id,
        "runtime_space_id": run.runtime_space_id,
        "status": run.status,
    }


def _append_strings(target: dict[str, object], key: str, value: str) -> None:
    values = target.setdefault(key, [])
    if not isinstance(values, list):
        return
    if value not in values:
        values.append(value)


def _extend_uuids(target: dict[str, object], key: str, values: list[UUID]) -> None:
    target_values = target.setdefault(key, [])
    if not isinstance(target_values, list):
        return
    existing = set(_uuid_list(target_values))
    for value in values:
        if value not in existing:
            target_values.append(value)
            existing.add(value)


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]
