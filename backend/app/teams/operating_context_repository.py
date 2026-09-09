from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from backend.app.agents.models import AgentProfile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.team_memory_context import memory_visibility_scopes


class TeamOperatingContextRepository:
    """Read team operating policy and memory context inputs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def team(self, *, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )

    def team_members(self, team: AgentTeam) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .join(AgentProfile, AgentProfile.id == AgentTeamMember.agent_profile_id)
                .options(contains_eager(AgentTeamMember.agent_profile))
                .where(
                    AgentTeamMember.workspace_id == team.workspace_id,
                    AgentTeamMember.agent_team_id == team.id,
                    AgentProfile.workspace_id == team.workspace_id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def workspace_memory_entries(
        self,
        workspace_id: UUID,
    ) -> list[WorkspaceMemoryEntry]:
        return list(
            self._session.scalars(
                select(WorkspaceMemoryEntry).where(
                    WorkspaceMemoryEntry.workspace_id == workspace_id,
                    WorkspaceMemoryEntry.status == "active",
                    WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
                    WorkspaceMemoryEntry.visibility_scope.in_(memory_visibility_scopes()),
                )
            )
        )

    def runtime_space(self, team: AgentTeam) -> RuntimeSpace | None:
        if team.runtime_space_id is None:
            return None
        return self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == team.workspace_id,
                RuntimeSpace.id == team.runtime_space_id,
            )
        )
