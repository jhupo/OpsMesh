from __future__ import annotations

from collections import Counter

from backend.app.teams.workspace_command_center_utils import (
    dict_value,
    int_value,
    list_value,
    set_value,
    string_list,
)


def team_summaries(overviews: list[dict[str, object]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for overview in overviews:
        team = dict_value(overview.get("team"))
        summary = dict_value(overview.get("summary"))
        summaries.append(
            {
                "team_id": overview.get("team_id"),
                "team_name": team.get("name"),
                "team_type": team.get("team_type"),
                "status": team.get("status"),
                "runtime_space_id": team.get("runtime_space_id"),
                "total_tasks": summary.get("total_tasks", 0),
                "needs_attention_tasks": summary.get("needs_attention_tasks", 0),
                "blocked_tasks": summary.get("blocked_tasks", 0),
                "high_risk_task_count": summary.get("high_risk_task_count", 0),
                "staffing_gap_count": summary.get("staffing_gap_count", 0),
                "overloaded_member_count": summary.get("overloaded_member_count", 0),
                "total_member_capacity": summary.get("total_member_capacity", 0),
                "active_member_task_count": summary.get("active_member_task_count", 0),
                "available_member_capacity": summary.get("available_member_capacity", 0),
                "delivery_health": summary.get("delivery_health", {}),
                "blocked_reasons": team_blocked_reasons(overview),
                "recommended_actions": summary.get("recommended_actions", []),
            }
        )
    return summaries


def task_items(overviews: list[dict[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for overview in overviews:
        team = dict_value(overview.get("team"))
        team_id = overview.get("team_id")
        for task in list_value(overview.get("tasks")):
            if not isinstance(task, dict):
                continue
            items.append(task_item(team_id, team, task))
    return sorted(
        items,
        key=lambda item: (
            -int_value(item.get("attention_score")),
            -int_value(item.get("priority")),
            str(item.get("team_name") or ""),
            str(item.get("title") or ""),
        ),
    )


def task_item(
    team_id: object,
    team: dict[str, object],
    task: dict[str, object],
) -> dict[str, object]:
    return {
        "team_id": team_id,
        "team_name": team.get("name"),
        "team_type": team.get("team_type"),
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "priority": task.get("priority", 0),
        "domain_type": task.get("domain_type"),
        "summary_status": task.get("summary_status"),
        "pending_phase": task.get("pending_phase"),
        "risk_level": task.get("risk_level"),
        "attention_score": task.get("attention_score", 0),
        "needs_attention": task.get("needs_attention", False),
        "blocked_reasons": task.get("blocked_reasons", []),
        "recommended_actions": task.get("recommended_actions", []),
        "step_status_counts": task.get("step_status_counts", {}),
        "active_run_count": task.get("active_run_count", 0),
        "last_activity_at": task.get("last_activity_at"),
    }


def blocked_reasons(
    overviews: list[dict[str, object]],
    employee_load: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for overview in overviews:
        add_overview_blockers(grouped, overview)
    for employee in employee_load:
        add_employee_blockers(grouped, employee)
    return [
        {
            "code": code,
            "count": item["count"],
            "team_ids": sorted(set_value(item["team_ids"]), key=str),
            "task_ids": sorted(set_value(item["task_ids"]), key=str),
            "agent_profile_ids": sorted(set_value(item["agent_profile_ids"]), key=str),
        }
        for code, item in sorted(grouped.items())
    ]


def add_overview_blockers(
    grouped: dict[str, dict[str, object]],
    overview: dict[str, object],
) -> None:
    team_id = overview.get("team_id")
    for task in list_value(overview.get("tasks")):
        if not isinstance(task, dict):
            continue
        for reason in string_list(task.get("blocked_reasons")):
            item = ensure_blocker(grouped, reason)
            item["count"] = int_value(item["count"]) + 1
            set_value(item["team_ids"]).add(team_id)
            set_value(item["task_ids"]).add(task.get("task_id"))
    for member in list_value(overview.get("members")):
        if not isinstance(member, dict):
            continue
        for reason in string_list(member.get("blocked_reasons")):
            item = ensure_blocker(grouped, reason)
            item["count"] = int_value(item["count"]) + 1
            set_value(item["team_ids"]).add(team_id)
            set_value(item["agent_profile_ids"]).add(member.get("agent_profile_id"))


def add_employee_blockers(
    grouped: dict[str, dict[str, object]],
    employee: dict[str, object],
) -> None:
    for reason in string_list(employee.get("blocked_reasons")):
        item = ensure_blocker(grouped, reason)
        item["count"] = int_value(item["count"]) + 1
        set_value(item["agent_profile_ids"]).add(employee.get("agent_profile_id"))
        set_value(item["team_ids"]).update(list_value(employee.get("active_team_ids")))
        set_value(item["task_ids"]).update(list_value(employee.get("active_task_ids")))


def ensure_blocker(grouped: dict[str, dict[str, object]], code: str) -> dict[str, object]:
    return grouped.setdefault(
        code,
        {
            "code": code,
            "count": 0,
            "team_ids": set(),
            "task_ids": set(),
            "agent_profile_ids": set(),
        },
    )


def team_blocked_reasons(overview: dict[str, object]) -> list[str]:
    reasons: set[str] = set()
    for task in list_value(overview.get("tasks")):
        if isinstance(task, dict):
            reasons.update(string_list(task.get("blocked_reasons")))
    for member in list_value(overview.get("members")):
        if isinstance(member, dict):
            reasons.update(string_list(member.get("blocked_reasons")))
    return sorted(reasons)


def summary(
    *,
    team_count: int,
    team_summaries_: list[dict[str, object]],
    task_items_: list[dict[str, object]],
    employee_load: list[dict[str, object]],
    blocked_reasons_: list[dict[str, object]],
    action_plan: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "active_team_count": team_count,
        "total_tasks": len(task_items_),
        "needs_attention_tasks": sum(1 for item in task_items_ if item["needs_attention"] is True),
        "blocked_tasks": sum(1 for item in task_items_ if list_value(item.get("blocked_reasons"))),
        "high_risk_task_count": sum(
            1 for item in task_items_ if item.get("risk_level") in {"critical", "high"}
        ),
        "employee_count": len(employee_load),
        "employee_at_capacity_count": sum(1 for item in employee_load if item["at_capacity"]),
        "employee_overloaded_count": sum(1 for item in employee_load if item["overloaded"]),
        "total_capacity": sum(int_value(item.get("total_capacity")) for item in employee_load),
        "active_employee_task_count": sum(
            int_value(item.get("active_task_count")) for item in employee_load
        ),
        "blocked_reason_count": len(blocked_reasons_),
        "cross_project_action_count": len(action_plan),
        "team_health_counts": dict(
            sorted(
                Counter(
                    str(dict_value(item.get("delivery_health")).get("status", "unknown"))
                    for item in team_summaries_
                ).items()
            )
        ),
    }
