from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.agents.service import AgentManagementService
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.marketplace import (
    MarketplaceInstallRequest,
    MarketplaceListingCreateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.capabilities.mcp_servers import McpServerService
from backend.app.capabilities.models import McpServer, Skill, WorkspaceSkillInstall
from backend.app.core.config import Settings
from backend.app.db.errors import (
    DatabaseConflictError,
    commit_or_raise_conflict,
    flush_or_raise_conflict,
)
from backend.app.db.pagination import page_scalars
from backend.app.marketplace.listing_payloads import (
    agent_create_request_from_listing,
    listing_review_type,
    listing_status,
    marketplace_source_checksum,
    mcp_server_create_request_from_listing,
    mcp_tool_requests_from_listing,
)
from backend.app.marketplace.models import MarketplaceListing, WorkspaceMarketplaceInstall
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.service import ResourceReviewService


class MarketplaceService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def list_public_listings(
        self,
        page: PageParams,
        *,
        listing_type: str,
        query: str | None = None,
    ) -> tuple[list[MarketplaceListing], int]:
        statement = select(MarketplaceListing).where(
            MarketplaceListing.listing_type == listing_type,
            MarketplaceListing.visibility == "public",
            MarketplaceListing.status == "public",
        )
        if query is not None:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    MarketplaceListing.name.ilike(pattern),
                    MarketplaceListing.summary.ilike(pattern),
                )
            )
        return self._page_marketplace_listings(
            statement.order_by(MarketplaceListing.created_at.desc()),
            page,
        )

    def create_workspace_listing(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        data: MarketplaceListingCreateRequest,
    ) -> MarketplaceListing:
        review = self._review_listing(workspace_id, data)
        status = listing_status(data.status, data.visibility, review.required)
        listing = MarketplaceListing(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            source_resource_id=data.source_resource_id,
            listing_type=data.listing_type,
            visibility=data.visibility,
            status=status,
            name=data.name,
            summary=data.summary,
            version=data.version,
            tags=data.tags,
            manifest=data.manifest,
            listing_metadata=data.metadata,
        )
        self._session.add(listing)
        self._session.flush()
        if review.required:
            ResourceReviewService(self._session, self._settings).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=owner_user_id,
                approval_type=listing_review_type(listing.listing_type),
                target_type="marketplace_listing",
                target_id=listing.id,
                target_name=listing.name,
                review=review,
                snapshot={
                    "id": str(listing.id),
                    "listing_type": listing.listing_type,
                    "visibility": listing.visibility,
                    "name": listing.name,
                    "summary": listing.summary,
                    "version": listing.version,
                    "tags": list(listing.tags),
                    "manifest": dict(listing.manifest),
                },
            )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=owner_user_id,
            action=(
                "marketplace_listing.review_requested"
                if review.required
                else "marketplace_listing.created"
            ),
            target_type="marketplace_listing",
            target_id=listing.id,
            metadata={
                "listing_type": listing.listing_type,
                "visibility": listing.visibility,
                "status": listing.status,
                "review_required": review.required,
                "review_risk_level": review.risk_level,
                "review_reasons": review.reasons,
            },
        )
        commit_or_raise_conflict(self._session, "Marketplace listing could not be created")
        self._session.refresh(listing)
        return listing

    def _review_listing(
        self,
        workspace_id: UUID,
        data: MarketplaceListingCreateRequest,
    ) -> ResourceReview:
        reviewer = ResourceReviewService(self._session, self._settings)
        if data.listing_type == "agent" and data.source_resource_id is not None:
            agent = self._session.get(AgentProfile, data.source_resource_id)
            if agent is not None and agent.workspace_id == workspace_id:
                return reviewer.review_agent_profile(
                    workspace_id=workspace_id,
                    visibility=data.visibility,
                    name=agent.name,
                    role=agent.role,
                    instructions=agent.instructions,
                    capabilities=dict(agent.capabilities or {}),
                    skills=dict(agent.skills or {}),
                    tool_policy=dict(agent.tool_policy or {}),
                    runtime_policy=dict(agent.runtime_policy or {}),
                    approval_policy=dict(agent.approval_policy or {}),
                )
            raise ValueError("Agent profile not found")
        if data.listing_type == "skill" and data.source_resource_id is not None:
            skill = self._session.get(Skill, data.source_resource_id)
            if skill is not None and skill.owner_workspace_id == workspace_id:
                return reviewer.review_skill(
                    workspace_id=workspace_id,
                    visibility=data.visibility,
                    manifest=dict(skill.manifest or {}),
                    capability_keys=list(skill.capability_keys or []),
                )
            raise ValueError("Skill not found")
        if data.listing_type == "mcp_server" and data.source_resource_id is not None:
            server = self._session.get(McpServer, data.source_resource_id)
            if server is not None and server.workspace_id == workspace_id:
                return reviewer.review_mcp_server(
                    workspace_id=workspace_id,
                    server_type=server.server_type,
                    connection=dict(server.connection or {}),
                    visibility=data.visibility,
                )
            raise ValueError("MCP server not found")
        if data.listing_type in {"agent", "skill", "mcp_server"}:
            raise ValueError("Marketplace listing source_resource_id is required")
        return reviewer.review_plugin(
            workspace_id=workspace_id,
            visibility=data.visibility,
            name=data.name,
            manifest=data.manifest,
        )

    def install_listing(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        listing_id: UUID,
        data: MarketplaceInstallRequest,
    ) -> WorkspaceMarketplaceInstall:
        listing = self._session.get(MarketplaceListing, listing_id)
        if listing is None or not self._listing_installable(workspace_id, listing):
            raise ValueError("Marketplace listing not found")
        if self._workspace_listing_install_exists(workspace_id, listing.id):
            raise DatabaseConflictError("Marketplace listing is already installed in workspace")
        installed_resource_id = self._provision_listing_resource(
            workspace_id=workspace_id,
            user_id=user_id,
            listing=listing,
            config=data.config,
        )
        install = WorkspaceMarketplaceInstall(
            workspace_id=workspace_id,
            marketplace_listing_id=listing.id,
            installed_by_user_id=user_id,
            installed_resource_id=installed_resource_id,
            listing_type=listing.listing_type,
            installed_name=listing.name,
            installed_version=listing.version,
            installed_manifest=dict(listing.manifest),
            config=data.config,
        )
        self._session.add(install)
        listing.install_count += 1
        flush_or_raise_conflict(
            self._session,
            "Marketplace listing is already installed in workspace",
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="marketplace_listing.installed",
            target_type="workspace_marketplace_install",
            target_id=install.id,
            metadata={
                "marketplace_listing_id": str(listing.id),
                "listing_type": listing.listing_type,
                "installed_resource_id": str(installed_resource_id)
                if installed_resource_id is not None
                else None,
            },
        )
        commit_or_raise_conflict(
            self._session,
            "Marketplace listing is already installed in workspace",
        )
        self._session.refresh(install)
        return install

    def _provision_listing_resource(
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

    def list_workspace_installs(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        listing_type: str | None = None,
    ) -> tuple[list[WorkspaceMarketplaceInstall], int]:
        statement = select(WorkspaceMarketplaceInstall).where(
            WorkspaceMarketplaceInstall.workspace_id == workspace_id,
            WorkspaceMarketplaceInstall.status == "active",
        )
        if listing_type is not None:
            statement = statement.where(WorkspaceMarketplaceInstall.listing_type == listing_type)
        statement = statement.order_by(WorkspaceMarketplaceInstall.created_at.desc())
        return page_scalars(self._session, statement, page)

    def _listing_installable(self, workspace_id: UUID, listing: MarketplaceListing) -> bool:
        if listing.visibility == "public":
            return listing.status == "public"
        if listing.workspace_id != workspace_id:
            return False
        return listing.status == "active"

    def _workspace_listing_install_exists(
        self,
        workspace_id: UUID,
        listing_id: UUID,
    ) -> bool:
        existing = self._session.scalar(
            select(WorkspaceMarketplaceInstall.id).where(
                WorkspaceMarketplaceInstall.workspace_id == workspace_id,
                WorkspaceMarketplaceInstall.marketplace_listing_id == listing_id,
            )
        )
        return existing is not None

    def _page_marketplace_listings(
        self,
        statement: Select[tuple[MarketplaceListing]],
        page: PageParams,
    ) -> tuple[list[MarketplaceListing], int]:
        return page_scalars(self._session, statement, page)


