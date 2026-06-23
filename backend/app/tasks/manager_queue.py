from collections import Counter
from uuid import UUID

from backend.app.core.typing import string_list
from backend.app.tasks.models import Task


def manager_queue_item(task: Task, diagnostics: dict[str, object]) -> dict[str, object]:
    manager = diagnostics.get("manager") if isinstance(diagnostics.get("manager"), dict) else {}
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    blocked_reasons = string_list(diagnostics.get("blocked_reasons"))
    manager_agent = manager.get("agent") if isinstance(manager.get("agent"), dict) else None
    manager_status = manager_status_for_queue(manager)
    summary_status = str(summary.get("status") or "unknown")
    needs_attention = summary_status != "healthy" or bool(blocked_reasons)
    return {
        "task_id": task.id,
        "team_id": task.agent_team_id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "domain_type": task.domain_type,
        "manager_agent_profile_id": manager.get("agent_profile_id"),
        "manager_agent_name": manager_agent.get("name") if manager_agent is not None else None,
        "manager_status": manager_status,
        "summary_status": summary_status,
        "pending_phase": pending_manager_phase(diagnostics),
        "needs_attention": needs_attention,
        "blocked_reasons": blocked_reasons,
        "recommended_actions": recommended_manager_actions(blocked_reasons),
        "acceptance_decisions": int(summary.get("acceptance_decisions") or 0),
        "follow_up_cycles": int(summary.get("follow_up_cycles") or 0),
        "step_status_counts": int_dict(summary.get("step_status_counts")),
        "last_activity_at": task.updated_at,
    }


def manager_queue_summary(items: list[dict[str, object]]) -> dict[str, object]:
    pending_phases = Counter(str(item["pending_phase"]) for item in items)
    manager_statuses = Counter(str(item["manager_status"]) for item in items)
    recommended_actions = Counter(
        action
        for item in items
        for action in item["recommended_actions"]
        if isinstance(action, str)
    )
    return {
        "needs_attention": len(items),
        "pending_phases": dict(sorted(pending_phases.items())),
        "manager_statuses": dict(sorted(manager_statuses.items())),
        "recommended_actions": dict(sorted(recommended_actions.items())),
        "team_operator_action_plan": manager_queue_team_action_plan(items),
    }


def manager_queue_team_action_plan(items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[UUID, dict[str, object]] = {}
    for item in items:
        team_id = item.get("team_id")
        task_id = item.get("task_id")
        if not isinstance(team_id, UUID) or not isinstance(task_id, UUID):
            continue
        if "request_manager_review" not in string_list(item.get("recommended_actions")):
            continue
        plan = grouped.setdefault(
            team_id,
            {
                "team_id": team_id,
                "action": "request_manager_review",
                "automation": "team_operator_action",
                "api_route": (
                    "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions"
                ),
                "task_ids": [],
                "task_step_ids": [],
                "count": 0,
                "reason": "manager_queue",
            },
        )
        plan["count"] = int(plan["count"]) + 1
        append_uuid(plan, "task_ids", task_id)

    plan_items = []
    for item in grouped.values():
        payload = {
            "action": item["action"],
            "task_ids": item["task_ids"],
            "task_step_ids": [],
            "reason": item["reason"],
            "metadata": {"source": "manager_queue"},
        }
        plan_items.append({**item, "payload_template": payload})
    return sorted(plan_items, key=lambda item: str(item["team_id"]))


def append_uuid(item: dict[str, object], key: str, value: UUID) -> None:
    values = item[key] if isinstance(item.get(key), list) else []
    existing = [entry for entry in values if isinstance(entry, UUID)]
    if value not in existing:
        existing.append(value)
    item[key] = sorted(existing, key=str)


def manager_status_for_queue(manager: dict[str, object]) -> str:
    if not manager.get("has_manager"):
        return "missing"
    agent = manager.get("agent")
    if not isinstance(agent, dict):
        return "unknown"
    status = agent.get("status")
    return status if isinstance(status, str) else "unknown"


def pending_manager_phase(diagnostics: dict[str, object]) -> str:
    blocked_reasons = set(string_list(diagnostics.get("blocked_reasons")))
    if any(reason.startswith("missing_manager") for reason in blocked_reasons):
        return "manager_setup"
    if "specialist_steps_incomplete" in blocked_reasons:
        return "specialist_execution"
    if "acceptance_decision_missing" in blocked_reasons:
        return "manager_acceptance"
    if "follow_up_missing" in blocked_reasons or "follow_up_incomplete" in blocked_reasons:
        return "follow_up"

    handoff_chain = diagnostics.get("handoff_chain")
    if isinstance(handoff_chain, list):
        for phase in handoff_chain:
            if not isinstance(phase, dict):
                continue
            if phase.get("status") not in {"completed", "not_required"}:
                value = phase.get("phase")
                return value if isinstance(value, str) else "unknown"
    return "none"


def recommended_manager_actions(blocked_reasons: list[str]) -> list[str]:
    actions: list[str] = []
    if "missing_manager" in blocked_reasons:
        actions.append("assign_manager")
    if any(reason.startswith("missing_manager_") for reason in blocked_reasons):
        actions.append("request_manager_review")
    if "acceptance_decision_missing" in blocked_reasons:
        actions.append("request_manager_review")
    if "follow_up_missing" in blocked_reasons:
        actions.append("request_manager_review")
    if "follow_up_incomplete" in blocked_reasons:
        actions.append("monitor_follow_up")
    if "specialist_steps_incomplete" in blocked_reasons:
        actions.append("monitor_specialists")
    return list(dict.fromkeys(actions))


def int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(item) for key, item in value.items() if isinstance(item, int)}
