from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.agents.payloads import AGENT_PROFILE_REVIEW_FIELDS, profile_snapshot
from backend.app.core.config import Settings
from backend.app.reviews.constants import REVIEW_TYPE_AGENT_PROFILE
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.service import ResourceReviewService


class AgentProfileReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def review_create(self, *, workspace_id: UUID, values: dict[str, object]) -> ResourceReview:
        return self._resource_reviews().review_agent_profile(
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

    def review_update(
        self,
        profile: AgentProfile,
        values: dict[str, Any],
    ) -> ResourceReview | None:
        if not AGENT_PROFILE_REVIEW_FIELDS.intersection(values):
            return None
        return self._resource_reviews().review_agent_profile(
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

    def request_review(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        profile: AgentProfile,
        review: ResourceReview,
    ) -> None:
        self._resource_reviews().request_resource_review(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            approval_type=REVIEW_TYPE_AGENT_PROFILE,
            target_type="agent_profile",
            target_id=profile.id,
            target_name=profile.name,
            review=review,
            snapshot=profile_snapshot(profile),
        )

    def _resource_reviews(self) -> ResourceReviewService:
        return ResourceReviewService(self._session, self._settings)
