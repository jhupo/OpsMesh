from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.marketplace import (
    HireTalentRequest,
    RoleRecommendation,
    TalentCandidateRecommendation,
    TalentInstallPinRequest,
    TalentInstallUpgradeRequest,
    TalentListingCreateRequest,
    TalentListingResponse,
    TalentRecommendationRequest,
    TalentRecommendationResponse,
    TalentUpgradeStatusResponse,
    WorkspaceAgentInstallResponse,
)
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

    def recommend_team(
        self,
        *,
        workspace_id: UUID,
        data: TalentRecommendationRequest,
    ) -> TalentRecommendationResponse:
        existing_roles = self._existing_team_roles(workspace_id, data.team_id)
        role_specs = _role_specs_for_request(data)
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
                for item in viable[: data.max_candidates_per_role]
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
            objective=data.objective,
            team_type=data.team_type,
            recommended_roles=recommended_roles,
            existing_team_roles=sorted(existing_roles),
            uncovered_roles=uncovered_roles,
        )

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
            current_talent_listing_id=listing.id,
            source_agent_profile_id=source.id,
            installed_agent_profile_id=installed_agent.id,
            hired_by_user_id=user_id,
            installed_version=listing.version,
            pinned_version=True,
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

        self._copy_agent_definition(source, agent)
        agent.version = target.version
        install.current_talent_listing_id = target.id
        install.source_agent_profile_id = source.id
        install.installed_version = target.version
        install.pinned_version = data.keep_pinned
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

    def _copy_agent_definition(self, source: AgentProfile, target: AgentProfile) -> None:
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


def _install_response(install: WorkspaceAgentInstall) -> WorkspaceAgentInstallResponse:
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
            "agent": install.installed_agent_profile,
        }
    )
