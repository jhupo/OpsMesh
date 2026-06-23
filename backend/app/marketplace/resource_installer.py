from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.service import AgentManagementService
from backend.app.audit.service import AuditService
from backend.app.capabilities.mcp_servers import McpServerService
from backend.app.capabilities.models import Skill, WorkspaceSkillInstall
from backend.app.core.config import Settings
from backend.app.db.errors import flush_or_raise_conflict
from backend.app.marketplace.listing_payloads import (
    agent_create_request_from_listing,
    marketplace_source_checksum,
    mcp_server_create_request_from_listing,
    mcp_tool_requests_from_listing,
)
from backend.app.marketplace.models import MarketplaceListing


class MarketplaceResourceInstaller:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def provision_listing_resource(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        listing: MarketplaceListing,
        config: dict[str, object],
    ) -> UUID | None:
        if listing.listing_type == "agent":
            agent = AgentManagementService(self._session, self._settings).create_agent(
                workspace_id=workspace_id,
                data=agent_create_request_from_listing(listing, config),
                actor_user_id=user_id,
                commit=False,
            )
            return agent.id
        if listing.listing_type == "skill":
            install = self._install_skill_listing(
                workspace_id=workspace_id,
                user_id=user_id,
                listing=listing,
                config=config,
            )
            return install.id
        if listing.listing_type == "mcp_server":
            service = McpServerService(self._session, settings=self._settings)
            server = service.create_mcp_server(
                workspace_id,
                mcp_server_create_request_from_listing(listing),
                actor_user_id=user_id,
                commit=False,
            )
            for tool in mcp_tool_requests_from_listing(listing):
                service.allow_mcp_tool(
                    workspace_id,
                    server.id,
                    tool,
                    actor_user_id=user_id,
                    commit=False,
                )
            return server.id
        return None

    def _install_skill_listing(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        listing: MarketplaceListing,
        config: dict[str, object],
    ) -> WorkspaceSkillInstall:
        source_skill = self._source_skill_for_listing(listing)
        install = WorkspaceSkillInstall(
            workspace_id=workspace_id,
            skill_id=source_skill.id,
            installed_by_user_id=user_id,
            installed_key=source_skill.key,
            installed_name=source_skill.name,
            installed_version=source_skill.version,
            installed_description=source_skill.description,
            installed_capability_keys=list(source_skill.capability_keys),
            installed_manifest=dict(source_skill.manifest),
            source_owner_workspace_id=source_skill.owner_workspace_id,
            source_visibility="marketplace",
            source_checksum=marketplace_source_checksum(listing),
            config=config,
        )
        self._session.add(install)
        flush_or_raise_conflict(self._session, "Skill is already installed in workspace")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill.installed",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(source_skill.id),
                "source": "marketplace",
                "marketplace_listing_id": str(listing.id),
            },
        )
        return install

    def _source_skill_for_listing(self, listing: MarketplaceListing) -> Skill:
        if listing.source_resource_id is not None:
            source = self._session.get(Skill, listing.source_resource_id)
            if source is not None:
                return source
        raise ValueError("Marketplace skill source not found")
