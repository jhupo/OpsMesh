from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam
from backend.app.teams.project_dashboard_constants import TERMINAL_TASK_STATUSES
from backend.app.teams.project_dashboard_repository import TeamProjectDashboardRepository
from backend.app.teams.project_dashboard_views import _summary, _task_item


class TeamProjectDashboardService:
    """Aggregate team tasks into a project-level delivery dashboard."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamProjectDashboardRepository(session)

    def get_dashboard(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        limit: int = 100,
    ) -> dict[str, object] | None:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return None

        task_statement = select(Task).where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id == team_id,
        )
        if not include_completed:
            task_statement = task_statement.where(~Task.status.in_(TERMINAL_TASK_STATUSES))
        tasks = list(
            self._session.scalars(
                task_statement.order_by(
                    Task.priority.desc(),
                    Task.updated_at.desc(),
                    Task.id.asc(),
                ).limit(limit)
            ).all()
        )
        task_ids = [task.id for task in tasks]
        steps_by_task = self._repo.steps_by_task(workspace_id, task_ids)
        runs_by_task = self._repo.runs_by_task(workspace_id, task_ids)
        latest_events = self._repo.latest_events(
            workspace_id,
            [run for runs in runs_by_task.values() for run in runs],
        )
        artifacts_by_task = self._repo.artifacts_by_task(workspace_id, task_ids)
        latest_messages = self._repo.latest_messages(workspace_id, task_ids)
        items = [
            _task_item(
                task,
                steps=steps_by_task.get(task.id, []),
                runs=runs_by_task.get(task.id, []),
                latest_events=latest_events,
                artifacts=artifacts_by_task.get(task.id, []),
                latest_message=latest_messages.get(task.id),
            )
            for task in tasks
        ]
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "team": {
                "id": team.id,
                "name": team.name,
                "team_type": team.team_type,
                "status": team.status,
                "manager_agent_profile_id": team.manager_agent_profile_id,
                "runtime_space_id": team.runtime_space_id,
            },
            "summary": _summary(items, limit=limit, include_completed=include_completed),
            "tasks": items,
        }
