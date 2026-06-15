from __future__ import annotations

from uuid import UUID

from backend.app.teams.command_center_constants import (
    COMMAND_CENTER_ACTION_SOURCES,
    NON_APPLICABLE_RUNTIME_ACTIONS,
    RUNTIME_OPERATOR_ACTIONS,
)
from backend.app.teams.command_center_utils import _int, _uuid_list, _uuid_value
from backend.app.teams.operator_actions import TEAM_OPERATOR_ACTIONS


def _group_applicable_actions(
    *,
    action_plan: list[object],
    sources: list[str] | None,
    actions: list[str] | None,
    max_actions: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    allowed_sources = set(sources or COMMAND_CENTER_ACTION_SOURCES)
    allowed_actions = set(actions or TEAM_OPERATOR_ACTIONS | RUNTIME_OPERATOR_ACTIONS)
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
        if action in NON_APPLICABLE_RUNTIME_ACTIONS:
            skipped.append(_skip(index, "manual_operator_review_required", item))
            continue
        if not isinstance(action, str) or action not in (
            TEAM_OPERATOR_ACTIONS | RUNTIME_OPERATOR_ACTIONS
        ):
            skipped.append(_skip(index, "unsupported_action", item))
            continue
        if action not in allowed_actions:
            continue
        automation = item.get("automation")
        if automation not in {"team_operator_action", "team_runtime_control"}:
            skipped.append(_skip(index, "unsupported_automation", item))
            continue
        if automation == "team_runtime_control" and action not in RUNTIME_OPERATOR_ACTIONS:
            skipped.append(_skip(index, "unsupported_runtime_action", item))
            continue
        if automation == "team_operator_action" and action not in TEAM_OPERATOR_ACTIONS:
            skipped.append(_skip(index, "unsupported_operator_action", item))
            continue

        task_step_ids = _uuid_list(item.get("task_step_ids"))
        agent_profile_id = _uuid_value(item.get("agent_profile_id"))
        if automation == "team_operator_action" and action == "reassign_step":
            if agent_profile_id is None:
                skipped.append(_skip(index, "missing_reassign_agent", item))
                continue
            if len(task_step_ids) != 1:
                skipped.append(_skip(index, "invalid_reassign_step_count", item))
                continue

        group_key = (
            f"{action}:{agent_profile_id}:{task_step_ids[0]}"
            if action == "reassign_step"
            else action
        )
        group = grouped.setdefault(
            group_key,
            {
                "action": action,
                "automation": automation,
                "agent_profile_id": agent_profile_id,
                "sources": [],
                "task_ids": [],
                "task_step_ids": [],
                "candidate_count": 0,
                "max_priority": 0,
            },
        )
        _append_strings(group, "sources", source)
        _extend_uuids(group, "task_ids", _uuid_list(item.get("task_ids")))
        _extend_uuids(group, "task_step_ids", task_step_ids)
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


def _runtime_action_result(
    item: dict[str, object],
    status: str,
    reason: str,
) -> dict[str, object]:
    return {
        "action": item["action"],
        "automation": item["automation"],
        "status": status,
        "sources": item["sources"],
        "task_ids": item["task_ids"],
        "task_step_ids": item["task_step_ids"],
        "agent_profile_id": item["agent_profile_id"],
        "candidate_count": item["candidate_count"],
        "response": {"reason": reason},
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
