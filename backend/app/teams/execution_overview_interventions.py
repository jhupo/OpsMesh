from __future__ import annotations

from uuid import UUID

from backend.app.teams.execution_overview_contracts import (
    ExecutionBottleneck,
    OperatorIntervention,
    SpecialistReassignment,
    StaffingGap,
    SummaryAction,
)
from backend.app.teams.execution_overview_utils import (
    dedupe_strings,
    severity_rank,
    string_list,
    uuid_list,
)


def operator_intervention_plan(
    *,
    recommended_actions: list[SummaryAction],
    bottlenecks: list[ExecutionBottleneck],
    staffing_gaps: list[StaffingGap],
    specialist_reassignments: list[SpecialistReassignment],
    task_items: list[dict[str, object]],
) -> list[OperatorIntervention]:
    grouped: dict[str, SummaryAction] = {}
    for item in recommended_actions:
        action = item.get("action")
        if not isinstance(action, str) or not action:
            continue
        if action == "reassign_step":
            continue
        grouped[action] = {
            "action": action,
            "count": item["count"],
            "task_ids": uuid_list(item.get("task_ids")),
        }
    for bottleneck in bottlenecks:
        action = bottleneck.get("recommended_action")
        if not isinstance(action, str) or not action:
            continue
        if action == "reassign_step":
            continue
        item = grouped.setdefault(action, {"action": action, "count": 0, "task_ids": []})
        item["count"] = max(item["count"], bottleneck["count"])
        merged_task_ids = set(item["task_ids"])
        merged_task_ids.update(bottleneck["task_ids"])
        item["task_ids"] = sorted(merged_task_ids, key=str)

    plan: list[OperatorIntervention] = []
    for item in grouped.values():
        action = str(item["action"])
        task_ids = uuid_list(item.get("task_ids"))
        task_step_ids = _intervention_step_ids(action, staffing_gaps)
        related_bottlenecks = [
            bottleneck
            for bottleneck in bottlenecks
            if bottleneck.get("recommended_action") == action
        ]
        severity = _highest_severity(
            [str(bottleneck.get("severity")) for bottleneck in related_bottlenecks]
        )
        reason_codes = _intervention_reason_codes(
            action=action,
            task_ids=task_ids,
            task_items=task_items,
            bottlenecks=related_bottlenecks,
        )
        plan.append(
            {
                "action": action,
                "category": _intervention_category(action),
                "severity": severity,
                "priority": _intervention_priority(severity, item["count"]),
                "count": item["count"],
                "task_ids": task_ids,
                "task_step_ids": task_step_ids,
                "automation": _intervention_automation(action),
                "operator_action": _operator_action_name(action),
                "api_route": _intervention_api_route(action),
                "payload_template": _intervention_payload_template(
                    action,
                    task_step_ids=task_step_ids,
                ),
                "reason_codes": reason_codes,
            }
        )
    return sorted(
        [*plan, *_reassign_step_interventions(specialist_reassignments)],
        key=lambda item: (-item["priority"], item["action"]),
    )


def _reassign_step_interventions(
    specialist_reassignments: list[SpecialistReassignment],
) -> list[OperatorIntervention]:
    interventions: list[OperatorIntervention] = []
    for reassignment in specialist_reassignments:
        task_id = reassignment.get("task_id")
        task_step_id = reassignment.get("task_step_id")
        agent_profile_id = reassignment.get("replacement_agent_profile_id")
        if not (
            isinstance(task_id, UUID)
            and isinstance(task_step_id, UUID)
            and isinstance(agent_profile_id, UUID)
        ):
            continue
        step_status = str(reassignment.get("step_status") or "unknown")
        severity = "high" if step_status == "failed" else "medium"
        reason = str(reassignment.get("reason") or "blocked_or_failed_specialist_step")
        interventions.append(
            {
                "action": "reassign_step",
                "category": "execution_flow",
                "severity": severity,
                "priority": _intervention_priority(severity, 1),
                "count": 1,
                "task_ids": [task_id],
                "task_step_ids": [task_step_id],
                "agent_profile_id": agent_profile_id,
                "current_agent_profile_id": reassignment.get("current_agent_profile_id"),
                "replacement_agent": {
                    "id": agent_profile_id,
                    "name": reassignment.get("replacement_agent_name"),
                    "role": reassignment.get("replacement_agent_role"),
                    "team_role": reassignment.get("replacement_team_role"),
                },
                "automation": "team_operator_action",
                "operator_action": "reassign_step",
                "api_route": _intervention_api_route("reassign_step"),
                "payload_template": {
                    "action": "reassign_step",
                    "task_step_ids": [task_step_id],
                    "agent_profile_id": agent_profile_id,
                    "reason": "team_execution_overview",
                    "metadata": {
                        "source": "team_execution_overview",
                        "reassignment_reason": reason,
                    },
                },
                "reason_codes": dedupe_strings(
                    [
                        reason,
                        f"step_status:{step_status}",
                        f"required_role:{reassignment['required_role']}"
                        if isinstance(reassignment.get("required_role"), str)
                        else "required_role:unknown",
                    ]
                ),
            }
        )
    return interventions


def _intervention_step_ids(
    action: str,
    staffing_gaps: list[StaffingGap],
) -> list[UUID]:
    if action != "add_or_hire_team_member":
        return []
    return sorted(
        {step_id for gap in staffing_gaps for step_id in uuid_list(gap.get("task_step_ids"))},
        key=str,
    )


def _intervention_reason_codes(
    *,
    action: str,
    task_ids: list[UUID],
    task_items: list[dict[str, object]],
    bottlenecks: list[ExecutionBottleneck],
) -> list[str]:
    task_id_set = set(task_ids)
    reasons = [
        str(bottleneck["code"])
        for bottleneck in bottlenecks
        if isinstance(bottleneck.get("code"), str)
    ]
    if action == "add_or_hire_team_member":
        reasons.append("staffing_gap")
    for task in task_items:
        task_id = task.get("task_id")
        if not isinstance(task_id, UUID) or task_id not in task_id_set:
            continue
        reasons.extend(string_list(task.get("blocked_reasons")))
        if isinstance(task.get("risk_level"), str):
            reasons.append(f"risk:{task['risk_level']}")
    return dedupe_strings(reasons)


def _highest_severity(severities: list[str]) -> str:
    valid = [
        severity for severity in severities if severity in {"critical", "high", "medium", "low"}
    ]
    if not valid:
        return "medium"
    return sorted(valid, key=severity_rank)[0]


def _intervention_priority(severity: str, count: int) -> int:
    base = {
        "critical": 100,
        "high": 80,
        "medium": 50,
        "low": 25,
    }.get(severity, 40)
    return base + min(max(count, 0), 10)


def _intervention_category(action: str) -> str:
    if action == "add_or_hire_team_member":
        return "staffing"
    if action in {"request_manager_review", "track_revision_follow_up"}:
        return "manager_review"
    if action in {"rebalance_member_load", "review_member_availability"}:
        return "team_capacity"
    if action in {
        "reassign_step",
        "schedule_downstream_steps",
        "unblock_or_reassign_specialist_work",
    }:
        return "execution_flow"
    if action in {"inspect_runtime_capacity", "review_pending_approvals"}:
        return "runtime_operations"
    return "diagnostics"


def _intervention_automation(action: str) -> str:
    if action in {
        "request_manager_review",
        "reassign_step",
        "schedule_downstream_steps",
        "requeue_blocked_steps",
    }:
        return "team_operator_action"
    if action == "add_or_hire_team_member":
        return "talent_market"
    if action in {
        "monitor_specialist_execution",
        "inspect_task_diagnostics",
        "review_high_risk_tasks",
    }:
        return "diagnostic"
    return "manual"


def _operator_action_name(action: str) -> str | None:
    if action in {
        "request_manager_review",
        "reassign_step",
        "schedule_downstream_steps",
        "requeue_blocked_steps",
    }:
        return action
    return None


def _intervention_api_route(action: str) -> str:
    if action == "add_or_hire_team_member":
        return (
            "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/talent-market/recommendations"
        )
    if _operator_action_name(action) is not None:
        return "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions"
    if action in {
        "monitor_specialist_execution",
        "inspect_task_diagnostics",
        "review_high_risk_tasks",
    }:
        return "GET /api/v1/workspaces/{workspace_id}/tasks/{task_id}/execution-diagnostics"
    return "GET /api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-overview"


def _intervention_payload_template(
    action: str,
    *,
    task_step_ids: list[UUID],
) -> dict[str, object]:
    operator_action = _operator_action_name(action)
    if operator_action is not None:
        return {
            "action": operator_action,
            "task_step_ids": task_step_ids,
            "reason": "team_execution_overview",
            "metadata": {"source": "team_execution_overview"},
        }
    if action == "add_or_hire_team_member":
        return {"max_candidates_per_role": 3}
    return {}
