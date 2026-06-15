from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.capabilities import (
    CapabilityCreateRequest,
    SkillCreateRequest,
    ToolGroupCreateRequest,
)
from backend.app.capabilities.models import (
    Capability,
    Skill,
    ToolGroup,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.db.pagination import page_scalars
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
    REVIEW_TYPE_CAPABILITY,
    REVIEW_TYPE_SKILL,
)
from backend.app.reviews.service import ResourceReviewService

T = TypeVar("T")
class CapabilityService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def list_capabilities(
        self,
        page: PageParams,
        category: str | None = None,
    ) -> tuple[list[Capability], int]:
        statement = select(Capability).where(Capability.status == "active")
        if category is not None:
            statement = statement.where(Capability.category == category)
        return self._page(statement.order_by(Capability.category.asc(), Capability.key.asc()), page)

    def create_capability(
        self,
        data: CapabilityCreateRequest,
        workspace_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Capability:
        review = ResourceReviewService(self._session, self._settings).review_capability(
            workspace_id=workspace_id,
            key=data.key,
            name=data.name,
            category=data.category,
            description=data.description,
            default_policy=data.default_policy,
        )
        status = RESOURCE_STATUS_PENDING_APPROVAL if review.required else RESOURCE_STATUS_ACTIVE
        capability = Capability(status=status, **data.model_dump())
        self._session.add(capability)
        flush_or_raise_conflict(self._session, "Capability key already exists")
        if review.required:
            ResourceReviewService(self._session, self._settings).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_CAPABILITY,
                target_type="capability",
                target_id=capability.id,
                target_name=capability.name,
                review=review,
                snapshot={
                    "id": str(capability.id),
                    "key": capability.key,
                    "name": capability.name,
                    "category": capability.category,
                    "description": capability.description,
                    "default_policy": dict(capability.default_policy),
                    "status": capability.status,
                },
            )
        commit_or_raise_conflict(self._session, "Capability key already exists")
        self._session.refresh(capability)
        return capability

    def list_skills(
        self,
        page: PageParams,
        workspace_id: UUID | None = None,
    ) -> tuple[list[Skill], int]:
        statement = select(Skill).where(Skill.status == "active")
        if workspace_id is not None:
            statement = statement.where(
                or_(
                    Skill.visibility == "public",
                    Skill.owner_workspace_id == workspace_id,
                )
            )
        else:
            statement = statement.where(Skill.visibility == "public")
        statement = statement.order_by(Skill.key.asc())
        return self._page(statement, page)

    def create_skill(
        self,
        data: SkillCreateRequest,
        workspace_id: UUID | None = None,
        actor_user_id: UUID | None = None,
    ) -> Skill:
        review = ResourceReviewService(self._session, self._settings).review_skill(
            workspace_id=workspace_id,
            visibility=data.visibility,
            manifest=data.manifest,
            capability_keys=data.capability_keys,
        )
        owner_workspace_id = (
            workspace_id
            if data.visibility == "private" or (review.required and workspace_id is not None)
            else None
        )
        status = RESOURCE_STATUS_PENDING_APPROVAL if review.required else RESOURCE_STATUS_ACTIVE
        skill = Skill(owner_workspace_id=owner_workspace_id, status=status, **data.model_dump())
        self._session.add(skill)
        flush_or_raise_conflict(self._session, "Skill version already exists")
        if review.required and workspace_id is not None:
            ResourceReviewService(self._session, self._settings).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_SKILL,
                target_type="skill",
                target_id=skill.id,
                target_name=skill.name,
                review=review,
                snapshot={
                    "id": str(skill.id),
                    "key": skill.key,
                    "name": skill.name,
                    "version": skill.version,
                    "visibility": skill.visibility,
                    "status": skill.status,
                    "capability_keys": list(skill.capability_keys),
                    "manifest": dict(skill.manifest),
                },
            )
        commit_or_raise_conflict(self._session, "Skill version already exists")
        self._session.refresh(skill)
        return skill

    def create_tool_group(self, data: ToolGroupCreateRequest) -> ToolGroup:
        group = ToolGroup(**data.model_dump())
        self._session.add(group)
        commit_or_raise_conflict(self._session, "Tool group key already exists")
        self._session.refresh(group)
        return group

    def list_tool_groups(self, page: PageParams) -> tuple[list[ToolGroup], int]:
        statement = (
            select(ToolGroup).where(ToolGroup.status == "active").order_by(ToolGroup.key.asc())
        )
        return self._page(statement, page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
