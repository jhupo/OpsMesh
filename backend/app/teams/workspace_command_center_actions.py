from backend.app.teams.workspace_command_center_utils import dict_value, int_value, list_value


def cross_project_action_plan(
    *,
    overviews: list[dict[str, object]],
    employee_load: list[dict[str, object]],
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for overview in overviews:
        actions.extend(team_actions(overview))
    for employee in employee_load:
        if employee.get("overloaded") is True:
            actions.append(employee_action(employee, action="redistribute_agent_work", priority=95))
        elif (
            employee.get("at_capacity") is True
            and int_value(employee.get("active_team_count")) > 1
        ):
            actions.append(
                employee_action(employee, action="rebalance_cross_team_capacity", priority=75)
            )
    return sorted(
        actions,
        key=lambda item: (
            -int_value(item.get("priority")),
            str(item.get("action") or ""),
            str(item.get("team_name") or ""),
        ),
    )


def team_actions(overview: dict[str, object]) -> list[dict[str, object]]:
    team = dict_value(overview.get("team"))
    team_id = overview.get("team_id")
    summary = dict_value(overview.get("summary"))
    actions: list[dict[str, object]] = []
    for item in list_value(summary.get("intervention_plan")):
        if not isinstance(item, dict):
            continue
        action = dict(item)
        action["source"] = "team_execution_overview"
        action["team_id"] = team_id
        action["team_name"] = team.get("name")
        actions.append(action)
    return actions


def employee_action(
    employee: dict[str, object],
    *,
    action: str,
    priority: int,
) -> dict[str, object]:
    severity = "critical" if employee.get("overloaded") is True else "high"
    reason = "agent_over_capacity" if employee.get("overloaded") is True else "agent_at_capacity"
    return {
        "source": "employee_load",
        "action": action,
        "category": "capacity",
        "severity": severity,
        "priority": priority,
        "reason": reason,
        "agent_profile_id": employee.get("agent_profile_id"),
        "agent_name": employee.get("agent_name"),
        "team_ids": employee.get("active_team_ids", []),
        "task_ids": employee.get("active_task_ids", []),
        "active_task_count": employee.get("active_task_count", 0),
        "total_capacity": employee.get("total_capacity", 0),
        "automation": "team_operator_action",
        "operator_action": "reassign_specialist",
        "api_route": "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions",
        "payload_template": {
            "action": "reassign_specialist",
            "reason": reason,
            "metadata": {"source": "workspace_command_center"},
        },
    }
