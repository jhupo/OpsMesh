from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.marketplace import HireTalentRequest, TalentListingCreateRequest
from backend.app.audit.service import AuditService
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.marketplace.models import TalentListing, WorkspaceAgentInstall
from backend.app.teams.models import AgentTeam, AgentTeamMember


class TalentMarketplaceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def publish_agent(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        data: TalentListingCreateRequest,
    ) -> TalentListing:
        agent = self._require_agent(workspace_id, data.agent_profile_id)
        listing = TalentListing(
            owner_user_id=owner_user_id,
            source_workspace_id=workspace_id,
            source_agent_profile_id=agent.id,
            title=data.title,
            role=agent.role,
            summary=data.summary or agent.description,
            skill_tags=data.skill_tags,
            capability_tags=data.capability_tags,
            required_tools=data.required_tools,
            default_team_role=data.default_team_role,
            risk_level=data.risk_level,
            listing_metadata=data.metadata,
            version=agent.version,
        )
        self._session.add(listing)
        flush_or_raise_conflict(self._session, "Agent is already published at this version")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=owner_user_id,
            action="talent_listing.published",
            target_type="talent_listing",
            target_id=listing.id,
            metadata={"agent_profile_id": str(agent.id), "title": listing.title},
        )
        commit_or_raise_conflict(self._session, "Agent is already published at this version")
        self._session.refresh(listing)
        return listing

    def list_public_listings(
        self,
        page: PageParams,
        *,
        query: str | None = None,
        role: str | None = None,
        skill: str | None = None,
    ) -> tuple[list[TalentListing], int]:
        statement = select(TalentListing).where(TalentListing.status == "public")
        if query is not None:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(TalentListing.title.ilike(pattern), TalentListing.summary.ilike(pattern))
            )
        if role is not None:
            statement = statement.where(TalentListing.role == role)
        if skill is not None:
            rows = self._session.scalars(statement.order_by(TalentListing.created_at.desc())).all()
            filtered = [row for row in rows if skill in row.skill_tags]
            return filtered[page.offset : page.offset + page.limit], len(filtered)
        return self._page(statement.order_by(TalentListing.created_at.desc()), page)

    def hire_agent(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        listing_id: UUID,
        data: HireTalentRequest,
    ) -> WorkspaceAgentInstall:
        listing = self._session.get(TalentListing, listing_id)
        if listing is None or listing.status != "public":
            raise ValueError("Talent listing not found")
        source = self._session.get(AgentProfile, listing.source_agent_profile_id)
        if source is None or source.status != "active":
            raise ValueError("Published agent profile is not available")

        installed_agent = AgentProfile(
            workspace_id=workspace_id,
            name=data.agent_name or source.name,
            role=source.role,
            description=source.description,
            instructions=source.instructions,
            model=source.model,
            model_settings=dict(source.model_settings),
            capabilities=dict(source.capabilities),
            skills=dict(source.skills),
            tool_policy=dict(source.tool_policy),
            runtime_policy=dict(source.runtime_policy),
            memory_policy=dict(source.memory_policy),
            approval_policy=dict(source.approval_policy),
            version=source.version,
        )
        self._session.add(installed_agent)
        self._session.flush()

        install = WorkspaceAgentInstall(
            workspace_id=workspace_id,
            talent_listing_id=listing.id,
            source_agent_profile_id=source.id,
            installed_agent_profile_id=installed_agent.id,
            hired_by_user_id=user_id,
        )
        self._session.add(install)
        if data.team_id is not None:
            self._add_to_team(
                workspace_id=workspace_id,
                team_id=data.team_id,
                agent_id=installed_agent.id,
                team_role=data.team_role or listing.default_team_role or source.role,
                order_index=data.order_index,
            )
        flush_or_raise_conflict(self._session, "Talent listing is already hired in workspace")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="talent_listing.hired",
            target_type="workspace_agent_install",
            target_id=install.id,
            metadata={
                "talent_listing_id": str(listing.id),
                "installed_agent_profile_id": str(installed_agent.id),
                "team_id": str(data.team_id) if data.team_id is not None else None,
            },
        )
        commit_or_raise_conflict(self._session, "Talent listing is already hired in workspace")
        self._session.refresh(install)
        return install

    def _add_to_team(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        agent_id: UUID,
        team_role: str,
        order_index: int,
    ) -> None:
        team = self._session.get(AgentTeam, team_id)
        if team is None or team.workspace_id != workspace_id:
            raise ValueError("Team not found")
        self._session.add(
            AgentTeamMember(
                workspace_id=workspace_id,
                agent_team_id=team_id,
                agent_profile_id=agent_id,
                team_role=team_role,
                order_index=order_index,
            )
        )

    def _require_agent(self, workspace_id: UUID, agent_id: UUID) -> AgentProfile:
        agent = self._session.get(AgentProfile, agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")
        return agent

    def _page(
        self,
        statement: Select[tuple[TalentListing]],
        page: PageParams,
    ) -> tuple[list[TalentListing], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
