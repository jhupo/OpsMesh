from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.teams.models import AgentTeamMember
from backend.app.teams.provider_readiness_member import (
    TeamProviderReadinessMemberBuilder,
)
from backend.app.teams.provider_readiness_repository import (
    TeamProviderReadinessRepository,
)
from backend.app.teams.provider_readiness_summary import provider_readiness_summary


class TeamProviderReadinessService:
    """Summarize whether team members have usable model providers."""

    def __init__(self, session: Session) -> None:
        self._repo = TeamProviderReadinessRepository(session)
        self._member_builder = TeamProviderReadinessMemberBuilder(session)

    def get_readiness(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
    ) -> dict[str, object]:
        items = [
            self._member_readiness(member)
            for member in self._repo.team_members(
                workspace_id=workspace_id,
                team_id=team_id,
            )
        ]
        return provider_readiness_summary(items)

    def _member_readiness(self, member: AgentTeamMember) -> dict[str, object]:
        agent = self._repo.agent(member.agent_profile_id)
        credential = self._repo.agent_model_provider_credential(agent)
        return self._member_builder.build(
            member=member,
            agent=agent,
            credential=credential,
        )
