from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.teams.execution_overview import TeamExecutionOverviewService
from backend.app.teams.workspace_command_center_actions import cross_project_action_plan
from backend.app.teams.workspace_command_center_load import WorkspaceEmployeeLoadBuilder
from backend.app.teams.workspace_command_center_repository import WorkspaceCommandCenterRepository
from backend.app.teams.workspace_command_center_views import (
    blocked_reasons,
    summary,
    task_items,
    team_summaries,
)


class WorkspaceCommandCenterService:
    """Build a workspace-level control-plane view across active teams."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = WorkspaceCommandCenterRepository(session)

    def get_command_center(
        self,
        *,
        workspace_id: UUID,
        include_completed: bool = False,
    ) -> dict[str, object]:
        teams = self._repo.active_teams(workspace_id)
        overviews = self._team_overviews(
            workspace_id=workspace_id,
            team_ids=[team.id for team in teams],
            include_completed=include_completed,
        )
        employee_load = self._employee_load(
            workspace_id=workspace_id,
            include_completed=include_completed,
        )
        team_summary_items = team_summaries(overviews)
        task_summary_items = task_items(overviews)
        blocked_reason_items = blocked_reasons(overviews, employee_load)
        action_plan = cross_project_action_plan(
            overviews=overviews,
            employee_load=employee_load,
        )
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
        self,
        *,
        workspace_id: UUID,
        team_ids: list[UUID],
        include_completed: bool,
    ) -> list[dict[str, object]]:
        overview_service = TeamExecutionOverviewService(self._session)
        overviews: list[dict[str, object]] = []
        for team_id in team_ids:
            overview = overview_service.get_overview(
                workspace_id=workspace_id,
                team_id=team_id,
                include_completed=include_completed,
            )
            if overview is not None:
                overviews.append(overview)
        return overviews

    def _employee_load(
        self,
        *,
        workspace_id: UUID,
        include_completed: bool,
    ) -> list[dict[str, object]]:
        teams = self._repo.active_teams(workspace_id)
        team_ids = [team.id for team in teams]
        members = self._repo.members(workspace_id, team_ids)
        tasks = self._repo.tasks(
            workspace_id=workspace_id,
            team_ids=team_ids,
            include_completed=include_completed,
        )
        task_ids = [task.id for task in tasks]
        return WorkspaceEmployeeLoadBuilder(
            teams=teams,
            members=members,
            agents=self._repo.agents(
                workspace_id,
                {member.agent_profile_id for member in members},
            ),
            tasks=tasks,
            active_steps=self._repo.active_steps(workspace_id, task_ids),
            active_runs=self._repo.active_runs(workspace_id, task_ids),
        ).build()
