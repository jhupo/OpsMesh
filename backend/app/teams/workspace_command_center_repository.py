from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.execution_overview_constants import (
    ACTIVE_RUN_STATUSES,
    ACTIVE_STEP_STATUSES,
    DONE_TASK_STATUSES,
)
from backend.app.teams.models import AgentTeam, AgentTeamMember


class WorkspaceCommandCenterRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_teams(self, workspace_id: UUID) -> list[AgentTeam]:
        return list(
            self._session.scalars(
                select(AgentTeam)
                .where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.status == "active",
                )
                .order_by(AgentTeam.name.asc(), AgentTeam.id.asc())
            )
        )

    def members(self, workspace_id: UUID, team_ids: list[UUID]) -> list[AgentTeamMember]:
        if not team_ids:
            return []
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id.in_(team_ids),
                )
                .order_by(
                    AgentTeamMember.agent_profile_id.asc(),
                    AgentTeamMember.order_index.asc(),
                    AgentTeamMember.id.asc(),
                )
            )
        )

    def agents(self, workspace_id: UUID, agent_ids: set[UUID]) -> dict[UUID, AgentProfile]:
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def tasks(
        self,
        *,
        workspace_id: UUID,
        team_ids: list[UUID],
        include_completed: bool,
    ) -> list[Task]:
        if not team_ids:
            return []
        statement = select(Task).where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id.in_(team_ids),
        )
        if not include_completed:
            statement = statement.where(~Task.status.in_(DONE_TASK_STATUSES))
        return list(self._session.scalars(statement))

    def active_steps(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskStep]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskStep).where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.task_id.in_(task_ids),
                    TaskStep.status.in_(ACTIVE_STEP_STATUSES),
                )
            )
        )

    def active_runs(self, workspace_id: UUID, task_ids: list[UUID]) -> list[AgentRun]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.task_id.in_(task_ids),
                    AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                )
            )
        )
