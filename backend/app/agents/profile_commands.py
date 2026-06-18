from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.model_validation import AgentModelValidator
from backend.app.agents.models import AgentProfile
from backend.app.agents.payloads import (
    AGENT_PROFILE_FIELDS,
    copy_json_value,
    normalize_create_payload,
    normalize_update_payload,
    profile_snapshot,
)
from backend.app.agents.reviews import AgentProfileReviewService
from backend.app.agents.versions import AgentVersionRecorder
from backend.app.api.schemas.agents import (
    AgentProfileCreateRequest,
    AgentProfileUpdateRequest,
)
from backend.app.core.config import Settings
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
)

AGENT_STATUS_ACTIVE = RESOURCE_STATUS_ACTIVE


class AgentProfileCommandService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings
        self._model_validator = AgentModelValidator(session)
        self._versions = AgentVersionRecorder(session)

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
        review_service = AgentProfileReviewService(self._session, self._settings)
        review = review_service.review_create(
            workspace_id=workspace_id,
            values=values,
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
            review_service.request_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                profile=profile,
                review=review,
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
        profile: AgentProfile,
        changes: AgentProfileUpdateRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        values = normalize_update_payload(changes, {})
        if not values:
            return profile
        if "status" in values:
            raise ValueError("Use archive or activate to change agent lifecycle status")
        before_snapshot = profile_snapshot(profile)
        if "model_provider_credential_id" in values:
            self._model_validator.validate_model_provider_credential(
                profile.workspace_id,
                values["model_provider_credential_id"],
            )
        self._model_validator.validate_agent_model_api(
            profile.workspace_id,
            values.get("model_provider_credential_id", profile.model_provider_credential_id),
            values.get("model_settings", profile.model_settings),
        )

        for field, value in values.items():
            setattr(profile, field, copy_json_value(field, value))
        review = AgentProfileReviewService(self._session, self._settings).review_update(
            profile,
            values,
        )
        if review is not None and review.required:
            profile.status = RESOURCE_STATUS_PENDING_APPROVAL
            AgentProfileReviewService(self._session, self._settings).request_review(
                workspace_id=profile.workspace_id,
                actor_user_id=actor_user_id,
                profile=profile,
                review=review,
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
