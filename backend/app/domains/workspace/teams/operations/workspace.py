from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.domains.workspace.teams.execution.overview import TeamExecutionOverviewService
from backend.app.domains.workspace.teams.operations.workspace_load import (
    WorkspaceEmployeeLoadBuilder,
)
from backend.app.domains.workspace.teams.operations.workspace_repository import (
    WorkspaceCommandCenterRepository,
)
from backend.app.domains.workspace.teams.operations.workspace_views import (
    blocked_reasons,
    dict_value,
    int_value,
    list_value,
    summary,
    task_items,
    team_summaries,
)


def cross_project_action_plan(
    *, overviews: list[dict[str, object]], employee_load: list[dict[str, object]]
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for overview in overviews:
        actions.extend(team_actions(overview))
    for employee in employee_load:
        if employee.get("overloaded") is True:
            actions.append(employee_action(employee, action="redistribute_agent_work", priority=95))
        elif (
            employee.get("at_capacity") is True and int_value(employee.get("active_team_count")) > 1
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
    employee: dict[str, object], *, action: str, priority: int
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


class WorkspaceCommandCenterService:
    """Build a workspace-level control-plane view across active teams."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = WorkspaceCommandCenterRepository(session)

    def get_command_center(
        self, *, workspace_id: UUID, include_completed: bool = False
    ) -> dict[str, object]:
        teams = self._repo.active_teams(workspace_id)
        overviews = self._team_overviews(
            workspace_id=workspace_id,
            team_ids=[team.id for team in teams],
            include_completed=include_completed,
        )
        employee_load = self._employee_load(
            workspace_id=workspace_id, include_completed=include_completed
        )
        team_summary_items = team_summaries(overviews)
        task_summary_items = task_items(overviews)
        blocked_reason_items = blocked_reasons(overviews, employee_load)
        action_plan = cross_project_action_plan(overviews=overviews, employee_load=employee_load)
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "summary": summary(
                team_count=len(teams),
                team_summaries_=team_summary_items,
                task_items_=task_summary_items,
                employee_load=employee_load,
                blocked_reasons_=blocked_reason_items,
                action_plan=action_plan,
            ),
            "team_summaries": team_summary_items,
            "project_task_items": task_summary_items,
            "employee_load": employee_load,
            "blocked_reasons": blocked_reason_items,
            "cross_project_action_plan": action_plan,
        }

    def _team_overviews(
        self, *, workspace_id: UUID, team_ids: list[UUID], include_completed: bool
    ) -> list[dict[str, object]]:
        overview_service = TeamExecutionOverviewService(self._session)
        overviews: list[dict[str, object]] = []
        for team_id in team_ids:
            overview = overview_service.get_overview(
                workspace_id=workspace_id, team_id=team_id, include_completed=include_completed
            )
            if overview is not None:
                overviews.append(overview)
        return overviews

    def _employee_load(
        self, *, workspace_id: UUID, include_completed: bool
    ) -> list[dict[str, object]]:
        teams = self._repo.active_teams(workspace_id)
        team_ids = [team.id for team in teams]
        members = self._repo.members(workspace_id, team_ids)
        tasks = self._repo.tasks(
            workspace_id=workspace_id, team_ids=team_ids, include_completed=include_completed
        )
        task_ids = [task.id for task in tasks]
        return WorkspaceEmployeeLoadBuilder(
            teams=teams,
            members=members,
            agents=self._repo.agents(workspace_id, {member.agent_profile_id for member in members}),
            tasks=tasks,
            active_steps=self._repo.active_steps(workspace_id, task_ids),
            active_runs=self._repo.active_runs(workspace_id, task_ids),
        ).build()
