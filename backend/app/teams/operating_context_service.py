from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.operating_context_repository import (
    TeamOperatingContextRepository,
)
from backend.app.teams.operating_policy_payloads import operating_policy_payload
from backend.app.teams.team_memory_context import memory_summary_payload


class TeamOperatingContextService:
    """Build the persistent operating policy and long-term memory context for a team."""

    def __init__(self, session: Session) -> None:
        self._repo = TeamOperatingContextRepository(session)

    def get_context(self, *, workspace_id: UUID, team_id: UUID) -> dict[str, object] | None:
        team = self._repo.team(workspace_id=workspace_id, team_id=team_id)
        if team is None:
            return None
        members = self._repo.team_members(team)
        return {
            "operating_policy": self.operating_policy(team=team, members=members),
            "memory_summary": self.memory_summary(team=team),
        }

    def operating_policy(
        self,
        *,
        team: AgentTeam,
        members: list[AgentTeamMember] | None = None,
    ) -> dict[str, object]:
        resolved_members = self._resolved_members(team, members)
        return operating_policy_payload(
            team=team,
            members=resolved_members,
            runtime_space=self._repo.runtime_space(team),
        )

    def memory_summary(self, *, team: AgentTeam) -> dict[str, object]:
        return memory_summary_payload(
            team=team,
            entries=self._repo.workspace_memory_entries(team.workspace_id),
        )

    def _resolved_members(
        self,
        team: AgentTeam,
        members: list[AgentTeamMember] | None,
    ) -> list[AgentTeamMember]:
        if members is not None and all(member.agent_profile is not None for member in members):
            return members
        return self._repo.team_members(team)
