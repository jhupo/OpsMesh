from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.teams.models import AgentTeamMember


class TeamProviderReadinessRepository:
    """Read provider readiness inputs from persistent team state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def team_members(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
    ) -> list[AgentTeamMember]:
        statement = (
            select(AgentTeamMember)
            .where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
        )
        return list(self._session.scalars(statement))

    def agent(self, agent_profile_id: UUID) -> AgentProfile | None:
        return self._session.get(AgentProfile, agent_profile_id)

    def agent_model_provider_credential(
        self,
        agent: AgentProfile | None,
    ) -> ModelProviderCredential | None:
        if agent is None:
            return None
        if agent.model_provider_credential_id is not None:
            return self._session.scalar(
                select(ModelProviderCredential).where(
                    ModelProviderCredential.workspace_id == agent.workspace_id,
                    ModelProviderCredential.id == agent.model_provider_credential_id,
                )
            )
        return self.workspace_default_credential(agent.workspace_id)

    def workspace_default_credential(
        self,
        workspace_id: UUID,
    ) -> ModelProviderCredential | None:
        return self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.is_default.is_(True),
                ModelProviderCredential.status == "active",
            )
        )
