from __future__ import annotations

from collections import Counter
from uuid import UUID

from backend.app.core.typing import dict_list, dict_or_empty, int_or_zero, string_list
from backend.app.tasks.operator_actions import TASK_OPERATOR_ACTIONS

RECOVERY_PLAN_SOURCES = {
    "collaboration_state",
    "handoff",
    "manager",
    "execution_diagnostics",
}


def build_recovery_plan(
    *,
    collaboration: dict[str, object],
    execution: dict[str, object],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    handoff_source_step_ids: list[UUID] = []
    handoff_reasons: list[str] = []
    for handoff in dict_list(collaboration.get("handoffs")):
        status = handoff.get("status")
        if status != "ready_for_downstream":
            continue
        step_id = handoff.get("task_step_id")
        if isinstance(step_id, UUID):
            handoff_source_step_ids.append(step_id)
        handoff_reasons.append(str(status))
    if handoff_source_step_ids:
        items.append(
            recovery_plan_item(
                action="schedule_downstream_steps",
                source="handoff",
                priority=90,
                reason="handoff_ready_for_downstream",
                task_step_ids=handoff_source_step_ids,
                blocked_reasons=["handoff_ready_for_downstream", *handoff_reasons],
            )
        )

    blocked_step_ids: list[UUID] = []
    blocked_reasons: list[str] = []
    for step in dict_list(execution.get("steps")):
        step_id = step.get("task_step_id")
        if step.get("status") != "blocked" or not isinstance(step_id, UUID):
            continue
        blocked_step_ids.append(step_id)
        blocked_reasons.extend(string_list(step.get("blocked_reasons")))
    if blocked_step_ids:
        items.append(
            recovery_plan_item(
                action="requeue_blocked_steps",
                source="execution_diagnostics",
                priority=80,
                reason="blocked_steps_detected",
                task_step_ids=blocked_step_ids,
                blocked_reasons=blocked_reasons or ["blocked_steps_detected"],
            )
        )

    manager = dict_or_empty(collaboration.get("manager"))
    manager_reasons = string_list(manager.get("blocked_reasons"))
    if needs_manager_review(manager_reasons):
        items.append(
            recovery_plan_item(
                action="request_manager_review",
                source="manager",
                priority=70,
                reason="manager_protocol_needs_review",
                task_step_ids=[],
                blocked_reasons=manager_reasons,
            )
        )

    summary_reasons = string_list(
        dict_or_empty(collaboration.get("summary")).get("blocked_reasons")
    )
    if "downstream_blocked" in summary_reasons and not blocked_step_ids:
        blocked_downstream_ids = [
            step_id
            for handoff in dict_list(collaboration.get("handoffs"))
            for step_id in uuid_list(handoff.get("blocked_downstream_step_ids"))
        ]
        if blocked_downstream_ids:
            items.append(
                recovery_plan_item(
                    action="requeue_blocked_steps",
                    source="collaboration_state",
                    priority=85,
                    reason="downstream_blocked",
                    task_step_ids=blocked_downstream_ids,
                    blocked_reasons=["downstream_blocked"],
                )
            )

    return sorted(
        dedupe_recovery_plan(items),
        key=lambda item: (-int_or_zero(item["priority"]), str(item["action"])),
    )


def recovery_plan_item(
    *,
    action: str,
    source: str,
    priority: int,
    reason: str,
    task_step_ids: list[UUID],
    blocked_reasons: list[str],
) -> dict[str, object]:
    task_step_ids = list(dict.fromkeys(task_step_ids))
    blocked_reasons = list(dict.fromkeys(blocked_reasons))
    return {
        "action": action,
        "source": source,
        "automation": "task_operator_action",
        "api_route": "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/operator-actions",
        "priority": priority,
        "reason": reason,
        "task_step_ids": task_step_ids,
        "blocked_reasons": blocked_reasons,
        "payload_template": {
            "action": action,
            "task_step_ids": task_step_ids,
            "reason": reason,
            "metadata": {"source": "task_collaboration_recovery", "diagnostic_source": source},
        },
    }


def dedupe_recovery_plan(items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for item in items:
        key = (str(item["action"]), str(item["source"]))
        if key not in grouped:
            grouped[key] = item
            continue
        existing = grouped[key]
        existing["task_step_ids"] = list(
            dict.fromkeys(
                [
                    *uuid_list(existing.get("task_step_ids")),
                    *uuid_list(item.get("task_step_ids")),
                ]
            )
        )
        existing["blocked_reasons"] = list(
            dict.fromkeys(
                [
                    *string_list(existing.get("blocked_reasons")),
                    *string_list(item.get("blocked_reasons")),
                ]
            )
        )
        payload = dict_or_empty(existing.get("payload_template"))
        payload["task_step_ids"] = existing["task_step_ids"]
        existing["payload_template"] = payload
    return list(grouped.values())


def filter_recovery_plan(
    plan: list[dict[str, object]],
    *,
    actions: list[str] | None,
    sources: list[str] | None,
) -> list[dict[str, object]]:
    allowed_actions = set(actions or TASK_OPERATOR_ACTIONS)
    allowed_sources = set(sources or RECOVERY_PLAN_SOURCES)
    return [
        item
        for item in plan
        if item.get("action") in allowed_actions and item.get("source") in allowed_sources
    ]


def skipped_recovery_plan_items(
    plan: list[dict[str, object]],
    *,
    selected: list[dict[str, object]],
    actions: list[str] | None,
    sources: list[str] | None,
) -> list[dict[str, object]]:
    selected_ids = {id(item) for item in selected}
    skipped: list[dict[str, object]] = []
    allowed_actions = set(actions or TASK_OPERATOR_ACTIONS)
    allowed_sources = set(sources or RECOVERY_PLAN_SOURCES)
    for item in plan:
        if id(item) in selected_ids:
            continue
        action = item.get("action")
        source = item.get("source")
        reason = "action_filtered" if action not in allowed_actions else "source_filtered"
        if source not in allowed_sources or action not in allowed_actions:
            skipped.append({"action": action, "source": source, "reason": reason})
    return skipped


def recovery_plan_summary(
    *,
    collaboration: dict[str, object],
    execution: dict[str, object],
    plan: list[dict[str, object]],
    skipped: list[dict[str, object]],
) -> dict[str, object]:
    action_counts = Counter(str(item["action"]) for item in plan)
    source_counts = Counter(str(item["source"]) for item in plan)
    return {
        "collaboration_status": dict_or_empty(collaboration.get("summary")).get("status"),
        "task_status": dict_or_empty(execution.get("task")).get("status"),
        "blocked_reasons": string_list(
            dict_or_empty(collaboration.get("summary")).get("blocked_reasons")
        ),
        "recommended_actions": string_list(
            dict_or_empty(collaboration.get("summary")).get("recommended_actions")
        ),
        "action_count": len(plan),
        "skipped_count": len(skipped),
        "action_counts": dict(sorted(action_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
    }


def recovery_plan_status(
    collaboration: dict[str, object],
    plan: list[dict[str, object]],
) -> str:
    if plan:
        return "actionable"
    summary = dict_or_empty(collaboration.get("summary"))
    if summary.get("status") in {"blocked", "needs_attention"}:
        return "needs_manual_attention"
    return "healthy"


def recovery_apply_status(
    dry_run: bool,
    applied_count: int,
    failed_count: int,
    action_plan: list[dict[str, object]],
) -> str:
    if dry_run:
        return "dry_run"
    if failed_count:
        return "partial_failure" if applied_count else "failed"
    if applied_count:
        return "applied"
    return "noop" if not action_plan else "no_changes"


def dry_run_recovery_result(item: dict[str, object]) -> dict[str, object]:
    return {
        "action": item.get("action"),
        "source": item.get("source"),
        "status": "would_apply",
        "task_step_ids": item.get("task_step_ids", []),
        "reason": item.get("reason"),
        "response": None,
    }


def needs_manager_review(blocked_reasons: list[str]) -> bool:
    reasons = set(blocked_reasons)
    return bool(
        reasons
        & {
            "acceptance_decision_missing",
            "follow_up_missing",
            "missing_manager_summary_step",
            "follow_up_incomplete",
        }
    ) or any(reason.startswith("missing_manager_") for reason in reasons)


def recovery_instruction(item: dict[str, object]) -> str | None:
    action = item.get("action")
    if action == "request_manager_review":
        return "Review the current collaboration state and decide the next delivery action."
    return None


def uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]
