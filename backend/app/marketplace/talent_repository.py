from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.marketplace.listing_payloads import (
    AgentDefinitionSnapshot,
)
from backend.app.marketplace.models import (
    TalentListing,
    WorkspaceAgentInstall,
)
from backend.app.teams.models import AgentTeam, AgentTeamMember


class TalentMarketplaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_public_listing(self, listing_id: UUID) -> TalentListing | None:
        listing = self._session.get(TalentListing, listing_id)
        if listing is None or listing.status != "public":
            return None
        return listing

    def get_install(self, workspace_id: UUID, install_id: UUID) -> WorkspaceAgentInstall | None:
        return self._session.scalar(
            select(WorkspaceAgentInstall).where(
                WorkspaceAgentInstall.id == install_id,
                WorkspaceAgentInstall.workspace_id == workspace_id,
                WorkspaceAgentInstall.status == "active",
            )
        )

    def list_installs(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[WorkspaceAgentInstall], int]:
        statement = (
            select(WorkspaceAgentInstall)
            .where(
                WorkspaceAgentInstall.workspace_id == workspace_id,
                WorkspaceAgentInstall.status == "active",
            )
            .order_by(WorkspaceAgentInstall.created_at.desc())
        )
        return page_scalars(self._session, statement, page)

    def existing_team_roles(self, workspace_id: UUID, team_id: UUID | None) -> set[str]:
        if team_id is None:
            return set()
        team = self._session.get(AgentTeam, team_id)
        if team is None or team.workspace_id != workspace_id:
            raise ValueError("Team not found")
        rows = self._session.scalars(
            select(AgentTeamMember).where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
            )
        )
        return {row.team_role for row in rows}

    def add_to_team(
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

    def latest_listing_for_install(self, install: WorkspaceAgentInstall) -> TalentListing | None:
        if install.source_agent_profile_id is None:
            return None
        return self._session.scalar(
            select(TalentListing)
            .where(
                TalentListing.source_agent_profile_id == install.source_agent_profile_id,
                TalentListing.status == "public",
            )
            .order_by(TalentListing.version.desc(), TalentListing.created_at.desc())
            .limit(1)
        )

    def resolve_upgrade_target(
        self,
        install: WorkspaceAgentInstall,
        target_listing_id: UUID | None,
    ) -> TalentListing:
        if target_listing_id is not None:
            target = self._session.get(TalentListing, target_listing_id)
            if (
                target is None
                or target.status != "public"
                or target.source_agent_profile_id != install.source_agent_profile_id
            ):
                raise ValueError("Talent listing upgrade target not found")
            return target
        target = self.latest_listing_for_install(install)
        if target is None:
            raise ValueError("Talent listing upgrade target not found")
        return target

    def review_install(
        self,
        workspace_id: UUID,
        listing_id: UUID,
        install_id: UUID | None,
    ) -> WorkspaceAgentInstall:
        statement = select(WorkspaceAgentInstall).where(
            WorkspaceAgentInstall.workspace_id == workspace_id,
            WorkspaceAgentInstall.status == "active",
        )
        if install_id is not None:
            statement = statement.where(WorkspaceAgentInstall.id == install_id)
        statement = statement.where(
            or_(
                WorkspaceAgentInstall.talent_listing_id == listing_id,
                WorkspaceAgentInstall.current_talent_listing_id == listing_id,
            )
        )
        install = self._session.scalar(statement.order_by(WorkspaceAgentInstall.created_at.desc()))
        if install is None:
            raise ValueError("Talent listing must be hired before review")
        return install

    def require_agent(self, workspace_id: UUID, agent_id: UUID) -> AgentProfile:
        agent = self._session.get(AgentProfile, agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")
        return agent


def copy_agent_definition(source: AgentDefinitionSnapshot, target: AgentProfile) -> None:
    target.role = source.role
    target.description = source.description
    target.instructions = source.instructions
    target.model = source.model
    target.model_settings = dict(source.model_settings)
    target.capabilities = dict(source.capabilities)
    target.skills = dict(source.skills)
    target.tool_policy = dict(source.tool_policy)
    target.runtime_policy = dict(source.runtime_policy)
    target.memory_policy = dict(source.memory_policy)
    target.approval_policy = dict(source.approval_policy)
