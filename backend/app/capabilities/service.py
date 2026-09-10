from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.capabilities.base import (
    CapabilityCreateRequest,
    CapabilityUpdateRequest,
    SkillCreateRequest,
    SkillUpdateRequest,
    ToolGroupCreateRequest,
    ToolGroupUpdateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import (
    Capability,
    Skill,
    ToolGroup,
)
from backend.app.capabilities.schema_validation import reject_embedded_secrets
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.db.pagination import page_scalars
from backend.app.reviews.approval_service import ResourceReviewApprovalService
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
    REVIEW_TYPE_CAPABILITY,
    REVIEW_TYPE_SKILL,
)
from backend.app.reviews.service import ResourcePolicyReviewBuilder

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
        review = ResourcePolicyReviewBuilder(self._session, self._settings).review_capability(
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
            ResourceReviewApprovalService(self._session).request_resource_review(
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

    def update_capability(
        self,
        capability_id: UUID,
        data: CapabilityUpdateRequest,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> Capability:
        capability = self._session.get(Capability, capability_id)
        if capability is None:
            raise ValueError("Capability not found")
        next_name = data.name or capability.name
        next_category = data.category or capability.category
        next_description = (
            data.description if data.description is not None else capability.description
        )
        next_policy = (
            data.default_policy
            if data.default_policy is not None
            else capability.default_policy
        )
        reject_embedded_secrets(next_policy, path="default_policy")
        review = ResourcePolicyReviewBuilder(self._session, self._settings).review_capability(
            workspace_id=workspace_id,
            key=capability.key,
            name=next_name,
            category=next_category,
            description=next_description,
            default_policy=next_policy,
        )
        before = _capability_snapshot(capability)
        if data.name is not None:
            capability.name = data.name
        if data.category is not None:
            capability.category = data.category
        if data.description is not None:
            capability.description = data.description
        if data.default_policy is not None:
            capability.default_policy = dict(data.default_policy)
        capability.status = (
            RESOURCE_STATUS_PENDING_APPROVAL
            if review.required
            else data.status or capability.status
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="capability.updated" if not review.required else "capability.review_requested",
            target_type="capability",
            target_id=capability.id,
            metadata={
                "before": before,
                "after": _capability_snapshot(capability),
                "review_required": review.required,
            },
        )
        if review.required:
            ResourceReviewApprovalService(self._session).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_CAPABILITY,
                target_type="capability",
                target_id=capability.id,
                target_name=capability.name,
                review=review,
                snapshot=_capability_snapshot(capability),
            )
        self._session.commit()
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
        review = ResourcePolicyReviewBuilder(self._session, self._settings).review_skill(
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
            ResourceReviewApprovalService(self._session).request_resource_review(
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

    def update_skill(
        self,
        skill_id: UUID,
        data: SkillUpdateRequest,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> Skill:
        skill = self._session.get(Skill, skill_id)
        if skill is None or skill.owner_workspace_id != workspace_id:
            raise ValueError("Workspace-owned skill not found")
        next_manifest = data.manifest if data.manifest is not None else skill.manifest
        next_capability_keys = (
            data.capability_keys
            if data.capability_keys is not None
            else skill.capability_keys
        )
        reject_embedded_secrets(next_manifest, path="manifest")
        review = ResourcePolicyReviewBuilder(self._session, self._settings).review_skill(
            workspace_id=workspace_id,
            visibility=data.visibility or skill.visibility,
            manifest=next_manifest,
            capability_keys=next_capability_keys,
        )
        before = _skill_snapshot(skill)
        for field_name in ("name", "description", "capability_keys", "manifest", "visibility"):
            if field_name in data.model_fields_set:
                setattr(skill, field_name, getattr(data, field_name))
        skill.status = (
            RESOURCE_STATUS_PENDING_APPROVAL
            if review.required
            else data.status or skill.status
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="skill.updated" if not review.required else "skill.review_requested",
            target_type="skill",
            target_id=skill.id,
            metadata={
                "before": before,
                "after": _skill_snapshot(skill),
                "review_required": review.required,
            },
        )
        if review.required:
            ResourceReviewApprovalService(self._session).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_SKILL,
                target_type="skill",
                target_id=skill.id,
                target_name=skill.name,
                review=review,
                snapshot=_skill_snapshot(skill),
            )
        self._session.commit()
        self._session.refresh(skill)
        return skill

    def create_tool_group(self, data: ToolGroupCreateRequest) -> ToolGroup:
        group = ToolGroup(**data.model_dump())
        self._session.add(group)
        commit_or_raise_conflict(self._session, "Tool group key already exists")
        self._session.refresh(group)
        return group

    def update_tool_group(
        self,
        group_id: UUID,
        data: ToolGroupUpdateRequest,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> ToolGroup:
        group = self._session.get(ToolGroup, group_id)
        if group is None:
            raise ValueError("Tool group not found")
        if data.tool_names is not None:
            _validate_tool_names(data.tool_names)
        before = _tool_group_snapshot(group)
        for field_name in ("name", "description", "tool_names", "status"):
            if field_name in data.model_fields_set:
                setattr(group, field_name, getattr(data, field_name))
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="tool_group.updated",
            target_type="tool_group",
            target_id=group.id,
            metadata={
                "before": before,
                "after": _tool_group_snapshot(group),
            },
        )
        self._session.commit()
        self._session.refresh(group)
        return group

    def list_tool_groups(self, page: PageParams) -> tuple[list[ToolGroup], int]:
        statement = (
            select(ToolGroup).where(ToolGroup.status == "active").order_by(ToolGroup.key.asc())
        )
        return self._page(statement, page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)


def _capability_snapshot(capability: Capability) -> dict[str, object]:
    return {
        "key": capability.key,
        "name": capability.name,
        "category": capability.category,
        "description": capability.description,
        "default_policy": dict(capability.default_policy),
        "status": capability.status,
    }


def _skill_snapshot(skill: Skill) -> dict[str, object]:
    return {
        "key": skill.key,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "capability_keys": list(skill.capability_keys),
        "manifest": dict(skill.manifest),
        "visibility": skill.visibility,
        "status": skill.status,
    }


def _tool_group_snapshot(group: ToolGroup) -> dict[str, object]:
    return {
        "key": group.key,
        "name": group.name,
        "description": group.description,
        "tool_names": list(group.tool_names),
        "status": group.status,
    }


def _validate_tool_names(tool_names: list[str]) -> None:
    if any(not isinstance(item, str) or not item.strip() for item in tool_names):
        raise ValueError("Tool group names cannot be empty")
    if len(set(tool_names)) != len(tool_names):
        raise ValueError("Tool group names cannot contain duplicates")
