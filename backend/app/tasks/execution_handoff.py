from collections import Counter
from uuid import UUID

from backend.app.tasks.models import Task


def handoff_queue_item(
    task: Task,
    step_payload: dict[str, object],
) -> dict[str, object] | None:
    handoff = step_payload.get("handoff")
    if not isinstance(handoff, dict):
        return None
    status = handoff.get("status")
    if not isinstance(status, str) or not status:
        return None
    return {
        "task_id": task.id,
        "task_title": task.title,
        "task_status": task.status,
        "task_priority": task.priority,
        "team_id": task.agent_team_id,
        "domain_type": task.domain_type,
        "task_step_id": step_payload.get("task_step_id"),
        "work_package_id": step_payload.get("work_package_id"),
        "step_title": step_payload.get("title"),
        "step_status": step_payload.get("status"),
        "assigned_agent": step_payload.get("assigned_agent"),
        "assignment_status": step_payload.get("assignment_status"),
        "handoff_status": status,
        "requires_handoff": handoff.get("requires_handoff") is True,
        "upstream_step_ids": uuid_values(handoff.get("upstream_step_ids")),
        "downstream_step_ids": uuid_values(handoff.get("downstream_step_ids")),
        "runnable_downstream_step_ids": uuid_values(handoff.get("runnable_downstream_step_ids")),
        "blocked_downstream_step_ids": uuid_values(handoff.get("blocked_downstream_step_ids")),
        "blocked_reasons": string_values(step_payload.get("blocked_reasons")),
        "recommended_actions": string_values(handoff.get("recommended_actions")),
        "last_activity_at": task.updated_at,
    }


def handoff_needs_attention(item: dict[str, object]) -> bool:
    return item["handoff_status"] in {
        "ready_for_downstream",
        "downstream_blocked",
        "handoff_in_progress",
        "final_delivery_ready",
    }


def handoff_queue_summary(items: list[dict[str, object]]) -> dict[str, object]:
    status_counts = Counter(str(item["handoff_status"]) for item in items)
    action_counts = Counter(
        action
        for item in items
        for action in item["recommended_actions"]
        if isinstance(action, str)
    )
    task_ids = {task_id for item in items if isinstance((task_id := item.get("task_id")), UUID)}
    return {
        "attention_handoffs": len(items),
        "tasks": len(task_ids),
        "handoff_status_counts": dict(sorted(status_counts.items())),
        "recommended_actions": dict(sorted(action_counts.items())),
        "team_operator_action_plan": handoff_queue_team_action_plan(items),
        "ready_handoffs": status_counts.get("ready_for_downstream", 0),
        "blocked_handoffs": status_counts.get("downstream_blocked", 0),
        "final_delivery_ready": status_counts.get("final_delivery_ready", 0),
    }


def handoff_queue_team_action_plan(items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[UUID, str], dict[str, object]] = {}
    for item in items:
        team_id = item.get("team_id")
        task_id = item.get("task_id")
        task_step_id = item.get("task_step_id")
        if not isinstance(team_id, UUID) or not isinstance(task_id, UUID):
            continue
        for action in string_values(item.get("recommended_actions")):
            if action not in {"request_manager_review", "schedule_downstream_steps"}:
                continue
            plan = grouped.setdefault(
                (team_id, action),
                {
                    "team_id": team_id,
                    "action": action,
                    "automation": "team_operator_action",
                    "api_route": (
                        "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions"
                    ),
                    "task_ids": [],
                    "task_step_ids": [],
                    "count": 0,
                    "reason": "handoff_queue",
                },
            )
            plan["count"] = int(plan["count"]) + 1
            append_uuid(plan, "task_ids", task_id)
            if action == "schedule_downstream_steps" and isinstance(task_step_id, UUID):
                append_uuid(plan, "task_step_ids", task_step_id)

    plan_items = []
    for item in grouped.values():
        payload = {
            "action": item["action"],
            "task_ids": item["task_ids"],
            "task_step_ids": item["task_step_ids"],
            "reason": item["reason"],
            "metadata": {"source": "handoff_queue"},
        }
        plan_items.append({**item, "payload_template": payload})
    return sorted(
        plan_items,
        key=lambda item: (str(item["team_id"]), str(item["action"])),
    )


def append_uuid(item: dict[str, object], key: str, value: UUID) -> None:
    values = item[key] if isinstance(item.get(key), list) else []
    existing = [entry for entry in values if isinstance(entry, UUID)]
    if value not in existing:
        existing.append(value)
    item[key] = sorted(existing, key=str)


def uuid_values(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]


def string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
