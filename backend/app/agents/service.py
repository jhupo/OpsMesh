from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.model_validation import AgentModelValidator
from backend.app.agents.models import AgentProfile, AgentProfileVersion
from backend.app.agents.payloads import (
    AGENT_PROFILE_FIELDS,
    AGENT_PROFILE_REVIEW_FIELDS,
    copy_json_value,
    datetime_or_none,
    normalize_create_payload,
    normalize_update_payload,
    profile_snapshot,
    rollback_reason,
    uuid_or_none,
)
from backend.app.agents.versions import AgentVersionRecorder
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agents import (
    AgentProfileCloneRequest,
    AgentProfileCreateRequest,
    AgentProfileRollbackRequest,
    AgentProfileUpdateRequest,
)
from backend.app.core.config import Settings
from backend.app.db.pagination import page_scalars
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
    RESOURCE_STATUS_REJECTED,
    REVIEW_TYPE_AGENT_PROFILE,
)
from backend.app.reviews.service import ResourceReview, ResourceReviewService

AGENT_STATUS_ACTIVE = RESOURCE_STATUS_ACTIVE
AGENT_STATUS_ARCHIVED = "archived"


class AgentManagementService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings
        self._model_validator = AgentModelValidator(session)
        self._versions = AgentVersionRecorder(session)

    def list_agents(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentProfile], int]:
        statement = select(AgentProfile).where(AgentProfile.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentProfile.status == status)
        statement = statement.order_by(AgentProfile.created_at.desc(), AgentProfile.id.desc())
        return page_scalars(self._session, statement, page)

    def get_agent(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile | None:
        return self._profile(workspace_id, agent_profile_id)

    def create_agent(
        self,
        workspace_id: UUID,
        data: AgentProfileCreateRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> AgentProfile:
        values = normalize_create_payload(data, {})
        self._model_validator.validate_model_provider_credential(
            workspace_id,
            values["model_provider_credential_id"],
        )
        self._model_validator.validate_agent_model_api(
            workspace_id,
            values["model_provider_credential_id"],
            values["model_settings"],
        )

        now = datetime.now(UTC)
        review = ResourceReviewService(self._session, self._settings).review_agent_profile(
            workspace_id=workspace_id,
            visibility="private",
            name=str(values["name"]),
            role=str(values["role"]),
            instructions=str(values["instructions"]),
            capabilities=dict(values["capabilities"]),
            skills=dict(values["skills"]),
            tool_policy=dict(values["tool_policy"]),
            runtime_policy=dict(values["runtime_policy"]),
            approval_policy=dict(values["approval_policy"]),
        )
        profile = AgentProfile(
            workspace_id=workspace_id,
            **values,
            status=RESOURCE_STATUS_PENDING_APPROVAL if review.required else AGENT_STATUS_ACTIVE,
            version=1,
            archived_at=None,
            last_versioned_at=now,
        )
        self._session.add(profile)
        self._session.flush()
        if review.required:
            ResourceReviewService(self._session, self._settings).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_AGENT_PROFILE,
                target_type="agent_profile",
                target_id=profile.id,
                target_name=profile.name,
                review=review,
                snapshot=profile_snapshot(profile),
            )
        self._versions.record_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile created",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.created" if not review.required else "agent.review_requested",
            changed_fields=sorted(AGENT_PROFILE_FIELDS),
            extra_metadata={
                "review_required": review.required,
                "review_risk_level": review.risk_level,
                "review_reasons": review.reasons,
            },
        )
        if commit:
            self._session.commit()
            self._session.refresh(profile)
        return profile

    def update_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        changes: AgentProfileUpdateRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        values = normalize_update_payload(
            changes,
            {},
        )
        if not values:
            return profile
        if "status" in values:
            raise ValueError("Use archive or activate to change agent lifecycle status")
        before_snapshot = profile_snapshot(profile)
        if "model_provider_credential_id" in values:
            self._model_validator.validate_model_provider_credential(
                workspace_id,
                values["model_provider_credential_id"],
            )
        self._model_validator.validate_agent_model_api(
            workspace_id,
            values.get("model_provider_credential_id", profile.model_provider_credential_id),
            values.get("model_settings", profile.model_settings),
        )

        for field, value in values.items():
            setattr(profile, field, copy_json_value(field, value))
        review = self._review_agent_update_if_needed(profile, values)
        if review is not None and review.required:
            profile.status = RESOURCE_STATUS_PENDING_APPROVAL
            ResourceReviewService(self._session, self._settings).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_AGENT_PROFILE,
                target_type="agent_profile",
                target_id=profile.id,
                target_name=profile.name,
                review=review,
                snapshot=profile_snapshot(profile),
            )
        self._versions.bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile updated",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action=(
                "agent.review_requested"
                if review is not None and review.required
                else "agent.updated"
            ),
            changed_fields=sorted(values),
            before_snapshot=before_snapshot,
            extra_metadata={
                "review_required": review.required if review is not None else False,
                "review_risk_level": review.risk_level if review is not None else None,
                "review_reasons": review.reasons if review is not None else [],
            },
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def _review_agent_update_if_needed(
        self,
        profile: AgentProfile,
        values: dict[str, Any],
    ) -> ResourceReview | None:
        if not AGENT_PROFILE_REVIEW_FIELDS.intersection(values):
            return None
        return ResourceReviewService(self._session, self._settings).review_agent_profile(
            workspace_id=profile.workspace_id,
            visibility="private",
            name=profile.name,
            role=profile.role,
            instructions=profile.instructions,
            capabilities=dict(profile.capabilities or {}),
            skills=dict(profile.skills or {}),
            tool_policy=dict(profile.tool_policy or {}),
            runtime_policy=dict(profile.runtime_policy or {}),
            approval_policy=dict(profile.approval_policy or {}),
        )

    def archive_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        if profile.status == AGENT_STATUS_ARCHIVED:
            return profile
        before_snapshot = profile_snapshot(profile)
        profile.status = AGENT_STATUS_ARCHIVED
        profile.archived_at = datetime.now(UTC)
        self._versions.bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile archived",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.archived",
            changed_fields=["archived_at", "status"],
            before_snapshot=before_snapshot,
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def activate_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        if profile.status == AGENT_STATUS_ACTIVE:
            return profile
        if profile.status in {RESOURCE_STATUS_PENDING_APPROVAL, RESOURCE_STATUS_REJECTED}:
            raise ValueError("Agent profile requires approval before activation")
        before_snapshot = profile_snapshot(profile)
        profile.status = AGENT_STATUS_ACTIVE
        profile.archived_at = None
        self._versions.bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile activated",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.activated",
            changed_fields=["archived_at", "status"],
            before_snapshot=before_snapshot,
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def delete_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> bool:
        _ = actor_user_id
        profile = self._profile(workspace_id, agent_profile_id)
        if profile is None:
            return False
        self._session.delete(profile)
        self._session.commit()
        return True

    def clone_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        overrides: AgentProfileCloneRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        source = self._require_profile(workspace_id, agent_profile_id)
        values = {
            field: copy_json_value(field, getattr(source, field)) for field in AGENT_PROFILE_FIELDS
        }
        values.update(
            normalize_update_payload(
                overrides,
                {},
            )
        )
        self._model_validator.validate_model_provider_credential(
            workspace_id,
            values["model_provider_credential_id"],
        )
        cloned = self.create_agent(workspace_id, values, actor_user_id)
        latest_version = self._session.scalar(
            select(AgentProfileVersion).where(
                AgentProfileVersion.workspace_id == workspace_id,
                AgentProfileVersion.agent_profile_id == cloned.id,
                AgentProfileVersion.version == 1,
            )
        )
        if latest_version is not None:
            latest_version.change_reason = f"Cloned from agent profile {source.id}"
            self._versions.audit_profile_change(
                cloned,
                actor_user_id=actor_user_id,
                action="agent.cloned",
                changed_fields=sorted(AGENT_PROFILE_FIELDS),
                extra_metadata={"source_agent_profile_id": str(source.id)},
            )
            self._session.commit()
            self._session.refresh(cloned)
        return cloned

    def rollback_agent_version(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        version: int,
        request: AgentProfileRollbackRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        historical = self._session.scalar(
            select(AgentProfileVersion).where(
                AgentProfileVersion.workspace_id == workspace_id,
                AgentProfileVersion.agent_profile_id == agent_profile_id,
                AgentProfileVersion.version == version,
            )
        )
        if historical is None:
            raise ValueError("Agent profile version not found")

        before_snapshot = profile_snapshot(profile)
        snapshot = historical.snapshot
        values = {field: snapshot.get(field) for field in AGENT_PROFILE_FIELDS if field in snapshot}
        missing = [field for field in AGENT_PROFILE_FIELDS if field not in values]
        if missing:
            raise ValueError("Agent profile version snapshot is incomplete")
        credential_id = uuid_or_none(values["model_provider_credential_id"])
        self._model_validator.validate_model_provider_credential(workspace_id, credential_id)
        self._model_validator.validate_agent_model_api(
            workspace_id,
            credential_id,
            values["model_settings"],
        )

        for field, value in values.items():
            if field == "model_provider_credential_id":
                value = credential_id
            setattr(profile, field, copy_json_value(field, value))
        status = str(snapshot.get("status") or AGENT_STATUS_ACTIVE)
        if status not in {AGENT_STATUS_ACTIVE, AGENT_STATUS_ARCHIVED}:
            raise ValueError("Agent profile version snapshot has invalid status")
        profile.status = status
        profile.archived_at = datetime_or_none(snapshot.get("archived_at"))
        if profile.status == AGENT_STATUS_ACTIVE:
            profile.archived_at = None
        elif profile.archived_at is None:
            profile.archived_at = datetime.now(UTC)

        self._versions.bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason=rollback_reason(request) or f"Rolled back to version {version}",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.rolled_back",
            changed_fields=sorted(AGENT_PROFILE_FIELDS),
            before_snapshot=before_snapshot,
            extra_metadata={"rolled_back_to_version": version},
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def list_versions(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentProfileVersion]:
        self._require_profile(workspace_id, agent_profile_id)
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
        self._require_profile(workspace_id, agent_profile_id)
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

    def _require_profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile:
        profile = self._profile(workspace_id, agent_profile_id)
        if profile is None:
            raise ValueError("Agent profile not found")
        return profile

    def _profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile | None:
        return self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_profile_id,
            )
        )
