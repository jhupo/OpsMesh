from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from backend.app.agents.model_provider_summary import agent_profile_response
from backend.app.agents.models import AgentProfile
from backend.app.agents.service import AgentManagementService
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agents import AgentProfileCreateRequest
from backend.app.api.schemas.capabilities import (
    McpServerCreateRequest,
    McpToolAllowRequest,
)
from backend.app.api.schemas.marketplace import (
    HireTalentRequest,
    HireTaskTalentRequest,
    MarketplaceInstallRequest,
    MarketplaceListingCreateRequest,
    MarketplaceListingResponse,
    RoleRecommendation,
    TalentCandidateRecommendation,
    TalentInstallPinRequest,
    TalentInstallUpgradeRequest,
    TalentListingCreateRequest,
    TalentListingMetricsResponse,
    TalentListingResponse,
    TalentListingReviewCreateRequest,
    TalentListingReviewResponse,
    TalentRecommendationRequest,
    TalentRecommendationResponse,
    TalentUpgradeStatusResponse,
    TaskTalentRecommendationResponse,
    WorkspaceAgentInstallResponse,
    WorkspaceMarketplaceInstallResponse,
)
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import McpServer, Skill, WorkspaceSkillInstall
from backend.app.capabilities.service import CapabilityService
from backend.app.core.config import Settings
from backend.app.db.errors import (
    DatabaseConflictError,
    commit_or_raise_conflict,
    flush_or_raise_conflict,
)
from backend.app.marketplace.models import (
    MarketplaceListing,
    TalentListing,
    TalentListingReview,
    WorkspaceAgentInstall,
    WorkspaceMarketplaceInstall,
)
from backend.app.reviews.constants import (
    RESOURCE_STATUS_PENDING_APPROVAL,
    REVIEW_TYPE_AGENT_PROFILE,
    REVIEW_TYPE_MCP_SERVER,
    REVIEW_TYPE_PLUGIN,
    REVIEW_TYPE_SKILL,
)
from backend.app.reviews.service import ResourceReview, ResourceReviewService
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage
from backend.app.teams.models import AgentTeam, AgentTeamMember

_AGENT_SNAPSHOT_METADATA_KEY = "agent_snapshot"
_PRIVATE_DEFINITION_KEYS = frozenset(
    {
        "agent_profile_id",
        "credential_id",
        "credential_reference_id",
        "credential_reference_ids",
        "installed_skill_id",
        "installed_skill_ids",
        "mcp_credential_reference_id",
        "mcp_credential_reference_ids",
        "mcp_server_id",
        "mcp_server_ids",
        "model_provider_credential_id",
        "runtime_id",
        "runtime_space_id",
        "self_hosted_runtime_id",
        "skill_install_id",
        "skill_install_ids",
        "source_agent_profile_id",
        "source_workspace_id",
        "workspace_id",
        "workspace_runtime_id",
        "workspace_skill_install_id",
        "workspace_skill_install_ids",
    }
)


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
        status = _listing_status(data.status, data.visibility, review.required)
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
                approval_type=_listing_review_type(listing.listing_type),
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
                data=_agent_create_request_from_listing(listing, config),
                actor_user_id=user_id,
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
            service = CapabilityService(self._session, settings=self._settings)
            server = service.create_mcp_server(
                workspace_id,
                _mcp_server_create_request_from_listing(listing),
                actor_user_id=user_id,
            )
            for tool in _mcp_tool_requests_from_listing(listing):
                service.allow_mcp_tool(
                    workspace_id,
                    server.id,
                    tool,
                    actor_user_id=user_id,
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
            source_checksum=_marketplace_source_checksum(listing),
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
        self._session.commit()
        self._session.refresh(install)
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
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

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
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


class TalentMarketplaceService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def publish_agent(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        data: TalentListingCreateRequest,
    ) -> TalentListing:
        agent = self._require_agent(workspace_id, data.agent_profile_id)
        metadata = dict(data.metadata)
        metadata[_AGENT_SNAPSHOT_METADATA_KEY] = asdict(_agent_marketplace_snapshot(agent))
        review = ResourceReviewService(self._session, self._settings).review_agent_profile(
            workspace_id=workspace_id,
            visibility="public",
            name=agent.name,
            role=agent.role,
            instructions=agent.instructions,
            capabilities=dict(agent.capabilities or {}),
            skills=dict(agent.skills or {}),
            tool_policy=dict(agent.tool_policy or {}),
            runtime_policy=dict(agent.runtime_policy or {}),
            approval_policy=dict(agent.approval_policy or {}),
        )
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
            listing_metadata=metadata,
            version=agent.version,
            status=RESOURCE_STATUS_PENDING_APPROVAL if review.required else "public",
        )
        self._session.add(listing)
        flush_or_raise_conflict(self._session, "Agent is already published at this version")
        if review.required:
            ResourceReviewService(self._session, self._settings).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=owner_user_id,
                approval_type=REVIEW_TYPE_AGENT_PROFILE,
                target_type="talent_listing",
                target_id=listing.id,
                target_name=listing.title,
                review=review,
                snapshot={
                    "id": str(listing.id),
                    "source_agent_profile_id": str(agent.id),
                    "title": listing.title,
                    "role": listing.role,
                    "summary": listing.summary,
                    "version": listing.version,
                    "skill_tags": list(listing.skill_tags),
                    "capability_tags": list(listing.capability_tags),
                    "required_tools": list(listing.required_tools),
                },
            )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=owner_user_id,
            action=(
                "talent_listing.review_requested"
                if review.required
                else "talent_listing.published"
            ),
            target_type="talent_listing",
            target_id=listing.id,
            metadata={
                "agent_profile_id": str(agent.id),
                "title": listing.title,
                "review_required": review.required,
                "review_risk_level": review.risk_level,
                "review_reasons": review.reasons,
            },
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

    def recommend_team(
        self,
        *,
        workspace_id: UUID,
        data: TalentRecommendationRequest,
    ) -> TalentRecommendationResponse:
        return self._recommend_for_role_specs(
            workspace_id=workspace_id,
            objective=data.objective,
            team_type=data.team_type,
            team_id=data.team_id,
            role_specs=_role_specs_for_request(data),
            max_candidates_per_role=data.max_candidates_per_role,
        )

    def _recommend_for_role_specs(
        self,
        *,
        workspace_id: UUID,
        objective: str,
        team_type: str,
        team_id: UUID | None,
        role_specs: list[_RoleSpec],
        max_candidates_per_role: int,
    ) -> TalentRecommendationResponse:
        existing_roles = self._existing_team_roles(workspace_id, team_id)
        listings = list(
            self._session.scalars(
                select(TalentListing)
                .where(TalentListing.status == "public")
                .order_by(TalentListing.created_at.desc())
            )
        )
        recommended_roles: list[RoleRecommendation] = []
        uncovered_roles: list[str] = []

        for index, spec in enumerate(role_specs, start=1):
            scored = [
                _score_listing(
                    listing,
                    role=spec.role,
                    skill_tags=spec.skill_tags,
                    capability_tags=spec.capability_tags,
                )
                for listing in listings
            ]
            viable = [item for item in scored if item.score > 0]
            viable.sort(key=lambda item: (-item.score, item.listing.created_at), reverse=False)
            candidates = [
                TalentCandidateRecommendation(
                    listing=TalentListingResponse.model_validate(item.listing),
                    score=round(item.score, 2),
                    matched_reasons=item.reasons,
                    missing_tags=item.missing_tags,
                )
                for item in viable[:max_candidates_per_role]
            ]
            if spec.role not in existing_roles:
                uncovered_roles.append(spec.role)
            recommended_roles.append(
                RoleRecommendation(
                    role=spec.role,
                    team_role=spec.team_role,
                    priority=index,
                    reason=spec.reason,
                    candidates=candidates,
                )
            )

        return TalentRecommendationResponse(
            objective=objective,
            team_type=team_type,
            recommended_roles=recommended_roles,
            existing_team_roles=sorted(existing_roles),
            uncovered_roles=uncovered_roles,
        )

    def recommend_for_task_staffing(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        max_candidates_per_role: int = 3,
        persist_message: bool = True,
    ) -> TaskTalentRecommendationResponse | None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        missing_packages = _missing_work_packages_from_task(task)
        role_specs = [_role_spec_for_missing_package(package) for package in missing_packages]
        response = self._recommend_for_role_specs(
            workspace_id=workspace_id,
            objective=task.title,
            team_type=_task_team_type(task),
            team_id=task.agent_team_id,
            role_specs=role_specs,
            max_candidates_per_role=max_candidates_per_role,
        )
        task_response = TaskTalentRecommendationResponse(
            task_id=task.id,
            objective=response.objective,
            team_type=response.team_type,
            recommended_roles=response.recommended_roles,
            existing_team_roles=response.existing_team_roles,
            uncovered_roles=response.uncovered_roles,
            missing_work_packages=missing_packages,
        )
        if persist_message:
            self._append_hr_recommendation_message(task, task_response)
            self._session.commit()
        return task_response

    def hire_for_task_staffing_gap(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        task_id: UUID,
        data: HireTaskTalentRequest,
    ) -> WorkspaceAgentInstall | None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        if task.agent_team_id is None:
            raise ValueError("Task is not assigned to a persistent team")
        package = _missing_work_package_by_id(task, data.work_package_id)
        if package is None:
            raise ValueError("Task work package does not have a staffing gap")

        install = self.hire_agent(
            workspace_id=workspace_id,
            user_id=user_id,
            listing_id=data.listing_id,
            data=HireTalentRequest(
                agent_name=data.agent_name,
                team_id=task.agent_team_id,
                team_role=data.team_role
                or _string_or_default(package.get("required_role"), "specialist"),
                order_index=data.order_index,
            ),
        )
        self._append_task_hire_message(task, install=install, package=package)
        self._session.commit()
        self._session.refresh(install)
        return install

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
        definition = _listing_agent_definition(listing, source)

        installed_agent = AgentProfile(
            workspace_id=workspace_id,
            name=data.agent_name or definition.name,
            role=definition.role,
            description=definition.description,
            instructions=definition.instructions,
            model=definition.model,
            model_settings=dict(definition.model_settings),
            capabilities=dict(definition.capabilities),
            skills=dict(definition.skills),
            tool_policy=dict(definition.tool_policy),
            runtime_policy=dict(definition.runtime_policy),
            memory_policy=dict(definition.memory_policy),
            approval_policy=dict(definition.approval_policy),
            version=listing.version,
        )
        self._session.add(installed_agent)
        self._session.flush()

        install = WorkspaceAgentInstall(
            workspace_id=workspace_id,
            talent_listing_id=listing.id,
            current_talent_listing_id=listing.id,
            source_agent_profile_id=source.id,
            installed_agent_profile_id=installed_agent.id,
            hired_by_user_id=user_id,
            installed_version=listing.version,
            pinned_version=True,
        )
        self._session.add(install)
        listing.install_count += 1
        if data.team_id is not None:
            self._add_to_team(
                workspace_id=workspace_id,
                team_id=data.team_id,
                agent_id=installed_agent.id,
                team_role=data.team_role or listing.default_team_role or definition.role,
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

    def get_listing(self, listing_id: UUID) -> TalentListing | None:
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
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

    def get_upgrade_status(
        self,
        *,
        workspace_id: UUID,
        install_id: UUID,
    ) -> TalentUpgradeStatusResponse | None:
        install = self.get_install(workspace_id, install_id)
        if install is None:
            return None
        latest = self._latest_listing_for_install(install)
        return TalentUpgradeStatusResponse(
            install=_install_response(install),
            latest_listing=TalentListingResponse.model_validate(latest) if latest else None,
            has_update=latest is not None and latest.version > install.installed_version,
            pinned_version=install.pinned_version,
        )

    def set_install_pin(
        self,
        *,
        workspace_id: UUID,
        install_id: UUID,
        data: TalentInstallPinRequest,
        user_id: UUID,
    ) -> WorkspaceAgentInstall | None:
        install = self.get_install(workspace_id, install_id)
        if install is None:
            return None
        install.pinned_version = data.pinned_version
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="talent_install.pin_updated",
            target_type="workspace_agent_install",
            target_id=install.id,
            metadata={"pinned_version": data.pinned_version},
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def upgrade_install(
        self,
        *,
        workspace_id: UUID,
        install_id: UUID,
        data: TalentInstallUpgradeRequest,
        user_id: UUID,
    ) -> WorkspaceAgentInstall | None:
        install = self.get_install(workspace_id, install_id)
        if install is None:
            return None
        target = self._resolve_upgrade_target(install, data.target_listing_id)
        if target.version <= install.installed_version:
            raise ValueError("Talent install is already at this version or newer")
        agent = self._session.get(AgentProfile, install.installed_agent_profile_id)
        source = self._session.get(AgentProfile, target.source_agent_profile_id)
        if agent is None or source is None or source.status != "active":
            raise ValueError("Published agent profile is not available")

        definition = _listing_agent_definition(target, source)
        self._copy_agent_definition(definition, agent)
        agent.version = target.version
        install.current_talent_listing_id = target.id
        install.source_agent_profile_id = source.id
        install.installed_version = target.version
        install.pinned_version = data.keep_pinned
        target.upgrade_count += 1
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="talent_install.upgraded",
            target_type="workspace_agent_install",
            target_id=install.id,
            metadata={
                "target_listing_id": str(target.id),
                "installed_agent_profile_id": str(agent.id),
                "version": target.version,
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def upsert_review(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        listing_id: UUID,
        data: TalentListingReviewCreateRequest,
    ) -> TalentListingReview:
        listing = self.get_listing(listing_id)
        if listing is None:
            raise ValueError("Talent listing not found")
        install = self._review_install(workspace_id, listing_id, data.workspace_agent_install_id)
        existing = self._session.scalar(
            select(TalentListingReview).where(
                TalentListingReview.workspace_id == workspace_id,
                TalentListingReview.talent_listing_id == listing_id,
                TalentListingReview.status == "active",
            )
        )
        if existing is None:
            review = TalentListingReview(
                workspace_id=workspace_id,
                talent_listing_id=listing_id,
                workspace_agent_install_id=install.id,
                user_id=user_id,
                rating=data.rating,
                title=data.title,
                body=data.body,
            )
            self._session.add(review)
            listing.review_count += 1
            listing.rating_sum += data.rating
        else:
            listing.rating_sum += data.rating - existing.rating
            existing.workspace_agent_install_id = install.id
            existing.user_id = user_id
            existing.rating = data.rating
            existing.title = data.title
            existing.body = data.body
            review = existing
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="talent_listing.reviewed",
            target_type="talent_listing_review",
            target_id=review.id,
            metadata={"talent_listing_id": str(listing_id), "rating": data.rating},
        )
        commit_or_raise_conflict(self._session, "Talent listing already reviewed in workspace")
        self._session.refresh(review)
        return review

    def list_reviews(
        self,
        listing_id: UUID,
        page: PageParams,
    ) -> tuple[list[TalentListingReview], int] | None:
        if self.get_listing(listing_id) is None:
            return None
        statement = (
            select(TalentListingReview)
            .where(
                TalentListingReview.talent_listing_id == listing_id,
                TalentListingReview.status == "active",
            )
            .order_by(TalentListingReview.created_at.desc())
        )
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

    def listing_metrics(self, listing_id: UUID) -> TalentListingMetricsResponse | None:
        listing = self.get_listing(listing_id)
        if listing is None:
            return None
        return TalentListingMetricsResponse(
            talent_listing_id=listing.id,
            install_count=listing.install_count,
            upgrade_count=listing.upgrade_count,
            review_count=listing.review_count,
            average_rating=listing.average_rating,
        )

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

    def _existing_team_roles(self, workspace_id: UUID, team_id: UUID | None) -> set[str]:
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

    def _latest_listing_for_install(self, install: WorkspaceAgentInstall) -> TalentListing | None:
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

    def _resolve_upgrade_target(
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
        target = self._latest_listing_for_install(install)
        if target is None:
            raise ValueError("Talent listing upgrade target not found")
        return target

    def _review_install(
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

    def _copy_agent_definition(
        self,
        source: AgentDefinitionSnapshot,
        target: AgentProfile,
    ) -> None:
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

    def _require_agent(self, workspace_id: UUID, agent_id: UUID) -> AgentProfile:
        agent = self._session.get(AgentProfile, agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")
        return agent

    def _append_hr_recommendation_message(
        self,
        task: Task,
        response: TaskTalentRecommendationResponse,
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type="hr.staffing_recommendation",
            body=f"HR found {len(response.missing_work_packages)} staffing gap(s).",
            payload={
                "missing_work_packages": response.missing_work_packages,
                "recommended_roles": [
                    recommendation.model_dump(mode="json")
                    for recommendation in response.recommended_roles
                ],
                "uncovered_roles": response.uncovered_roles,
            },
        )

    def _append_task_hire_message(
        self,
        task: Task,
        *,
        install: WorkspaceAgentInstall,
        package: dict[str, object],
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            agent_profile_id=install.installed_agent_profile_id,
            message_type="hr.hire_confirmed",
            body=f"Hired agent for work package {package.get('package_id', 'unknown')}.",
            payload={
                "work_package_id": package.get("package_id"),
                "required_role": package.get("required_role"),
                "required_skills": package.get("required_skills", []),
                "workspace_agent_install_id": str(install.id),
                "talent_listing_id": str(install.talent_listing_id),
                "installed_agent_profile_id": str(install.installed_agent_profile_id),
            },
        )

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


@dataclass(frozen=True)
class _RoleSpec:
    role: str
    team_role: str
    reason: str
    skill_tags: tuple[str, ...]
    capability_tags: tuple[str, ...]


@dataclass(frozen=True)
class _ScoredListing:
    listing: TalentListing
    score: float
    reasons: list[str]
    missing_tags: list[str]


_TEAM_ROLE_PRESETS: dict[str, tuple[_RoleSpec, ...]] = {
    "research": (
        _RoleSpec(
            "project_manager",
            "manager",
            "拆解目标、协调专家、控制交付节奏。",
            ("planning", "coordination"),
            (),
        ),
        _RoleSpec(
            "researcher",
            "research_specialist",
            "收集资料、验证来源、沉淀研究证据。",
            ("research", "market"),
            ("web.search",),
        ),
        _RoleSpec(
            "analyst",
            "data_analyst",
            "整理数据、发现趋势、输出可执行结论。",
            ("analysis", "data"),
            ("data.analysis",),
        ),
    ),
    "novel": (
        _RoleSpec(
            "editor",
            "chief_editor",
            "维护世界观、节奏和章节质量。",
            ("writing", "editing"),
            (),
        ),
        _RoleSpec(
            "writer",
            "chapter_writer",
            "按大纲生成章节内容并保持角色一致。",
            ("writing", "story"),
            ("longform.write",),
        ),
        _RoleSpec(
            "reviewer",
            "continuity_reviewer",
            "检查设定冲突、伏笔和人物动机。",
            ("review", "continuity"),
            (),
        ),
    ),
    "software": (
        _RoleSpec(
            "project_manager",
            "tech_lead",
            "拆分需求、安排实现顺序、验收交付。",
            ("planning", "architecture"),
            (),
        ),
        _RoleSpec(
            "software_engineer",
            "backend_engineer",
            "实现后端服务、工具调用和任务编排。",
            ("backend", "python"),
            ("code.execute",),
        ),
        _RoleSpec(
            "qa_engineer",
            "qa_specialist",
            "设计测试、复现问题、验证回归。",
            ("testing", "quality"),
            (),
        ),
    ),
    "design": (
        _RoleSpec(
            "product_manager",
            "product_manager",
            "明确用户目标、定义范围和验收标准。",
            ("product", "planning"),
            (),
        ),
        _RoleSpec(
            "designer",
            "visual_designer",
            "产出界面视觉、素材和交互稿。",
            ("design", "ui"),
            ("image.generate",),
        ),
        _RoleSpec(
            "reviewer",
            "design_reviewer",
            "检查一致性、可用性和交付质量。",
            ("review", "ux"),
            (),
        ),
    ),
    "general": (
        _RoleSpec(
            "project_manager",
            "manager",
            "拆解目标、安排人员、跟踪进度。",
            ("planning", "coordination"),
            (),
        ),
        _RoleSpec(
            "researcher",
            "research_specialist",
            "补齐信息、收集资料、形成判断依据。",
            ("research",),
            ("web.search",),
        ),
        _RoleSpec(
            "operator",
            "operator",
            "执行工具调用、整理产物、推动任务完成。",
            ("operations",),
            (),
        ),
    ),
}


def _role_specs_for_request(data: TalentRecommendationRequest) -> list[_RoleSpec]:
    preset = list(_TEAM_ROLE_PRESETS.get(data.team_type, _TEAM_ROLE_PRESETS["general"]))
    role_tags = tuple(_normalize_tag(tag) for tag in data.skill_tags if tag)
    capability_tags = tuple(_normalize_tag(tag) for tag in data.capability_tags if tag)
    if not data.required_roles:
        return [
            _RoleSpec(
                role=spec.role,
                team_role=spec.team_role,
                reason=spec.reason,
                skill_tags=tuple(dict.fromkeys((*spec.skill_tags, *role_tags))),
                capability_tags=tuple(dict.fromkeys((*spec.capability_tags, *capability_tags))),
            )
            for spec in preset
        ]
    preset_by_role = {spec.role: spec for spec in preset}
    specs: list[_RoleSpec] = []
    for role in data.required_roles:
        normalized_role = _normalize_role(role)
        default = preset_by_role.get(normalized_role)
        default_skills = default.skill_tags if default is not None else ()
        default_capabilities = default.capability_tags if default is not None else ()
        specs.append(
            _RoleSpec(
                role=normalized_role,
                team_role=default.team_role if default is not None else normalized_role,
                reason=default.reason if default is not None else "老板需求中明确要求该岗位。",
                skill_tags=tuple(dict.fromkeys((*default_skills, *role_tags))),
                capability_tags=tuple(dict.fromkeys((*default_capabilities, *capability_tags))),
            )
        )
    return specs


def _missing_work_packages_from_task(task: Task) -> list[dict[str, object]]:
    project_plan = task.project_plan if isinstance(task.project_plan, dict) else None
    if project_plan is None:
        return []
    packages = project_plan.get("work_packages", [])
    if not isinstance(packages, list):
        return []

    missing: list[dict[str, object]] = []
    for raw_package in packages:
        if not isinstance(raw_package, dict):
            continue
        if raw_package.get("assigned_agent_profile_id") is not None:
            continue
        role = _string_or_default(raw_package.get("required_role"), "specialist")
        if role == "project_manager":
            continue
        missing.append(
            {
                "package_id": _string_or_default(raw_package.get("package_id"), "unknown"),
                "title": _string_or_default(raw_package.get("title"), role),
                "required_role": role,
                "required_skills": _string_list(raw_package.get("required_skills")),
                "expected_artifacts": _string_list(raw_package.get("expected_artifacts")),
            }
        )
    return missing


def _missing_work_package_by_id(task: Task, work_package_id: str) -> dict[str, object] | None:
    return next(
        (
            package
            for package in _missing_work_packages_from_task(task)
            if package.get("package_id") == work_package_id
        ),
        None,
    )


def _role_spec_for_missing_package(package: dict[str, object]) -> _RoleSpec:
    role = _string_or_default(package.get("required_role"), "specialist")
    return _RoleSpec(
        role=_normalize_role(role),
        team_role=role,
        reason=f"Work package {package.get('package_id', 'unknown')} has no assigned agent.",
        skill_tags=tuple(
            _normalize_tag(skill) for skill in _string_list(package.get("required_skills"))
        ),
        capability_tags=(),
    )


def _task_team_type(task: Task) -> str:
    snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else None
    if snapshot is None:
        return "general"
    team = snapshot.get("team")
    if not isinstance(team, dict):
        return "general"
    return _string_or_default(team.get("team_type"), "general")


def _score_listing(
    listing: TalentListing,
    *,
    role: str,
    skill_tags: tuple[str, ...],
    capability_tags: tuple[str, ...],
) -> _ScoredListing:
    score = 0.0
    reasons: list[str] = []
    missing_tags: list[str] = []
    listing_skills = {_normalize_tag(tag) for tag in listing.skill_tags}
    listing_capabilities = {_normalize_tag(tag) for tag in listing.capability_tags}
    normalized_role = _normalize_role(listing.role)

    if normalized_role == role:
        score += 5
        reasons.append("岗位匹配")
    elif role in normalized_role or normalized_role in role:
        score += 2
        reasons.append("岗位相近")

    for tag in skill_tags:
        if tag in listing_skills:
            score += 1.5
            reasons.append(f"技能匹配: {tag}")
        else:
            missing_tags.append(tag)

    for tag in capability_tags:
        if tag in listing_capabilities:
            score += 1
            reasons.append(f"能力匹配: {tag}")
        else:
            missing_tags.append(tag)

    if listing.risk_level == "low":
        score += 0.25
        reasons.append("低风险")

    return _ScoredListing(
        listing=listing,
        score=score,
        reasons=reasons,
        missing_tags=list(dict.fromkeys(missing_tags)),
    )


def _normalize_role(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_tag(value: str) -> str:
    return value.strip().lower()


def _string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _install_response(install: WorkspaceAgentInstall) -> WorkspaceAgentInstallResponse:
    db_session = Session.object_session(install)
    if db_session is None:
        db_session = Session.object_session(install.installed_agent_profile)
    if db_session is None:
        raise ValueError("Talent install response requires an attached database session")
    agent = agent_profile_response(db_session, install.installed_agent_profile)
    return WorkspaceAgentInstallResponse.model_validate(
        {
            "id": install.id,
            "created_at": install.created_at,
            "updated_at": install.updated_at,
            "workspace_id": install.workspace_id,
            "talent_listing_id": install.talent_listing_id,
            "current_talent_listing_id": install.current_talent_listing_id,
            "source_agent_profile_id": install.source_agent_profile_id,
            "installed_agent_profile_id": install.installed_agent_profile_id,
            "hired_by_user_id": install.hired_by_user_id,
            "installed_version": install.installed_version,
            "pinned_version": install.pinned_version,
            "status": install.status,
            "agent": agent,
        }
    )


def marketplace_install_response(
    install: WorkspaceMarketplaceInstall,
) -> WorkspaceMarketplaceInstallResponse:
    return WorkspaceMarketplaceInstallResponse.model_validate(
        {
            "id": install.id,
            "created_at": install.created_at,
            "updated_at": install.updated_at,
            "workspace_id": install.workspace_id,
            "marketplace_listing_id": install.marketplace_listing_id,
            "installed_by_user_id": install.installed_by_user_id,
            "installed_resource_id": install.installed_resource_id,
            "listing_type": install.listing_type,
            "installed_name": install.installed_name,
            "installed_version": install.installed_version,
            "installed_manifest": install.installed_manifest,
            "config": install.config,
            "status": install.status,
            "listing": MarketplaceListingResponse.model_validate(install.listing),
        }
    )


def _agent_create_request_from_listing(
    listing: MarketplaceListing,
    config: dict[str, object],
) -> AgentProfileCreateRequest:
    manifest = dict(listing.manifest)
    payload = _mapping_from_manifest(manifest, "agent")
    override_name = config.get("agent_name") if isinstance(config, dict) else None
    payload.setdefault("name", override_name if isinstance(override_name, str) else listing.name)
    payload.setdefault("role", _string_or_default(manifest.get("role"), "agent"))
    payload.setdefault("description", listing.summary)
    payload.setdefault("instructions", _string_or_default(manifest.get("instructions"), ""))
    return AgentProfileCreateRequest.model_validate(payload)


def _mcp_server_create_request_from_listing(listing: MarketplaceListing) -> McpServerCreateRequest:
    manifest = dict(listing.manifest)
    payload = _mapping_from_manifest(manifest, "mcp_server")
    payload.setdefault("name", listing.name)
    payload.setdefault("server_type", _string_or_default(manifest.get("server_type"), "stdio"))
    connection = manifest.get("connection")
    payload.setdefault("connection", dict(connection) if isinstance(connection, dict) else {})
    payload["visibility"] = "private"
    return McpServerCreateRequest.model_validate(payload)


def _mcp_tool_requests_from_listing(listing: MarketplaceListing) -> list[McpToolAllowRequest]:
    raw_tools = listing.manifest.get("tools") if isinstance(listing.manifest, dict) else None
    if not isinstance(raw_tools, list):
        return []
    requests: list[McpToolAllowRequest] = []
    for item in raw_tools:
        if isinstance(item, str):
            requests.append(McpToolAllowRequest(tool_name=item))
        elif isinstance(item, dict):
            requests.append(McpToolAllowRequest.model_validate(item))
    return requests


def _mapping_from_manifest(
    manifest: dict[str, object],
    key: str,
) -> dict[str, object]:
    nested = manifest.get(key)
    return dict(nested) if isinstance(nested, dict) else {}


def _listing_status(
    requested_status: str | None,
    visibility: str,
    review_required: bool,
) -> str:
    if review_required:
        return RESOURCE_STATUS_PENDING_APPROVAL
    if visibility == "public":
        return "public"
    if requested_status in {"draft", "archived"}:
        return requested_status
    return "active"


def _listing_review_type(listing_type: str) -> str:
    if listing_type == "agent":
        return REVIEW_TYPE_AGENT_PROFILE
    if listing_type == "skill":
        return REVIEW_TYPE_SKILL
    if listing_type == "mcp_server":
        return REVIEW_TYPE_MCP_SERVER
    return REVIEW_TYPE_PLUGIN


def _marketplace_source_checksum(listing: MarketplaceListing) -> str:
    material = f"{listing.id}:{listing.listing_type}:{listing.version}"
    return sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentDefinitionSnapshot:
    name: str
    role: str
    description: str
    instructions: str
    model: str
    model_settings: dict[str, object]
    capabilities: dict[str, object]
    skills: dict[str, object]
    tool_policy: dict[str, object]
    runtime_policy: dict[str, object]
    memory_policy: dict[str, object]
    approval_policy: dict[str, object]


def _listing_agent_definition(
    listing: TalentListing,
    source: AgentProfile,
) -> AgentDefinitionSnapshot:
    snapshot = listing.listing_metadata.get(_AGENT_SNAPSHOT_METADATA_KEY)
    if isinstance(snapshot, dict):
        return AgentDefinitionSnapshot(
            name=_non_empty_string_or_default(snapshot.get("name"), source.name),
            role=_non_empty_string_or_default(snapshot.get("role"), source.role),
            description=_non_empty_string_or_default(
                snapshot.get("description"),
                source.description,
            ),
            instructions=_non_empty_string_or_default(
                snapshot.get("instructions"),
                source.instructions,
            ),
            model=_non_empty_string_or_default(snapshot.get("model"), source.model),
            model_settings=_dict_or_empty(snapshot.get("model_settings")),
            capabilities=_dict_or_empty(snapshot.get("capabilities")),
            skills=_dict_or_empty(snapshot.get("skills")),
            tool_policy=_dict_or_empty(snapshot.get("tool_policy")),
            runtime_policy=_dict_or_empty(snapshot.get("runtime_policy")),
            memory_policy=_dict_or_empty(snapshot.get("memory_policy")),
            approval_policy=_dict_or_empty(snapshot.get("approval_policy")),
        )
    return _agent_marketplace_snapshot(source)


def _agent_marketplace_snapshot(agent: AgentProfile) -> AgentDefinitionSnapshot:
    return AgentDefinitionSnapshot(
        name=agent.name,
        role=agent.role,
        description=agent.description,
        instructions=agent.instructions,
        model=agent.model,
        model_settings=_safe_definition_dict(agent.model_settings),
        capabilities=_safe_definition_dict(agent.capabilities),
        skills=_safe_definition_dict(agent.skills),
        tool_policy=_safe_definition_dict(agent.tool_policy),
        runtime_policy=_safe_definition_dict(agent.runtime_policy),
        memory_policy=_safe_definition_dict(agent.memory_policy),
        approval_policy=_safe_definition_dict(agent.approval_policy),
    )


def _safe_definition_dict(value: object) -> dict[str, object]:
    cleaned = _safe_definition_value(value)
    return cleaned if isinstance(cleaned, dict) else {}


def _safe_definition_value(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, child in value.items():
            if key in _PRIVATE_DEFINITION_KEYS or key.endswith("_id") or key.endswith("_ids"):
                continue
            cleaned[key] = _safe_definition_value(child)
        return cleaned
    if isinstance(value, list):
        return [_safe_definition_value(item) for item in value]
    return value


def _dict_or_empty(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _non_empty_string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def review_response(review: TalentListingReview) -> TalentListingReviewResponse:
    return TalentListingReviewResponse.model_validate(review)
