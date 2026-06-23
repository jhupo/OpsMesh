from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile, AgentProfileVersion
from backend.app.api.pagination import PageParams
from backend.app.db.pagination import page_scalars


class AgentProfileQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile | None:
        return self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_profile_id,
            )
        )

    def require_profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile:
        profile = self.profile(workspace_id, agent_profile_id)
        if profile is None:
            raise ValueError("Agent profile not found")
        return profile

    def list_versions(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentProfileVersion]:
        self.require_profile(workspace_id, agent_profile_id)
        statement = (
            select(AgentProfileVersion)
            .where(
                AgentProfileVersion.workspace_id == workspace_id,
                AgentProfileVersion.agent_profile_id == agent_profile_id,
            )
            .order_by(AgentProfileVersion.version.desc())
        )
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self._session.scalars(statement).all())

    def list_agent_versions(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        page: PageParams,
    ) -> tuple[list[AgentProfileVersion], int]:
        self.require_profile(workspace_id, agent_profile_id)
        statement = select(AgentProfileVersion).where(
            AgentProfileVersion.workspace_id == workspace_id,
            AgentProfileVersion.agent_profile_id == agent_profile_id,
        )
        statement = statement.order_by(AgentProfileVersion.version.desc())
        return page_scalars(self._session, statement, page)

    def count(self, workspace_id: UUID, *, status: str | None = None) -> int:
        statement = (
            select(func.count())
            .select_from(AgentProfile)
            .where(AgentProfile.workspace_id == workspace_id)
        )
        if status is not None:
            statement = statement.where(AgentProfile.status == status)
        return int(self._session.scalar(statement) or 0)
