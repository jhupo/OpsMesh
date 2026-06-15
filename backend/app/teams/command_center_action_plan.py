from __future__ import annotations

from backend.app.teams.command_center_utils import _dict, _int, _list
from backend.app.teams.runtime import TEAM_RUNTIME_RUNNING, TEAM_RUNTIME_STALL_THRESHOLD


def _provider_action_plan(provider_readiness: dict[str, object]) -> list[dict[str, object]]:
    return _source_action_plan(
        source="provider_readiness",
        items=_list(provider_readiness.get("action_plan")),
    )


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


def _runtime_action_plan(runtime_state: object) -> list[dict[str, object]]:
    status = getattr(runtime_state, "status", None)
    workspace_runtime_id = getattr(runtime_state, "workspace_runtime_id", None)
    runtime_status = getattr(runtime_state, "runtime_status", None)
    metadata = _dict(getattr(runtime_state, "metadata", {}))
    stall_count = _int(metadata.get("stall_count"))
    stalled_at = metadata.get("stalled_at")
    if stalled_at or stall_count >= TEAM_RUNTIME_STALL_THRESHOLD:
        return [
            {
                "source": "team_runtime",
                "source_index": 0,
                "automation": "team_runtime_control",
                "action": "review_team_runtime_stall",
                "priority": 120,
                "reason": metadata.get("stall_reason") or "team_runtime_stalled",
                "stall_count": stall_count,
                "stall_threshold": _int(metadata.get("stall_threshold"))
                or TEAM_RUNTIME_STALL_THRESHOLD,
                "stalled_at": stalled_at,
                "task_ids": [],
                "task_step_ids": [],
            }
        ]
    if status != TEAM_RUNTIME_RUNNING:
        return [
            {
                "source": "team_runtime",
                "source_index": 0,
                "automation": "team_runtime_control",
                "action": "start_team_runtime",
                "priority": 90,
                "reason": "team_runtime_not_running",
                "task_ids": [],
                "task_step_ids": [],
            }
        ]
    if workspace_runtime_id is None or runtime_status != "running":
        return [
            {
                "source": "team_runtime",
                "source_index": 0,
                "automation": "team_runtime_control",
                "action": "ensure_team_runtime",
                "priority": 95,
                "reason": "workspace_runtime_not_running",
                "workspace_runtime_id": workspace_runtime_id,
                "runtime_status": runtime_status,
                "task_ids": [],
                "task_step_ids": [],
            }
        ]
    return []


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
