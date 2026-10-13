from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.profiles.payloads import AGENT_PROFILE_REVIEW_FIELDS, profile_snapshot
from opsmesh.governance.reviews.approval_service import ResourceReviewApprovalService
from opsmesh.governance.reviews.constants import REVIEW_TYPE_AGENT_PROFILE
from opsmesh.governance.reviews.contracts import ResourceReviewResult
from opsmesh.governance.reviews.service import ResourcePolicyReviewService
from opsmesh.shared.config import Settings


class AgentProfileReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def review_profile(self, profile: AgentProfile) -> ResourceReviewResult:
        return self._resource_reviews().review_agent_profile(
            workspace_id=profile.workspace_id,
            visibility="private",
            name=profile.name,
            role=profile.role,
            instructions=profile.instructions,
            capabilities=dict(profile.capabilities),
            skills=dict(profile.skills),
            tool_policy=dict(profile.tool_policy),
            runtime_policy=dict(profile.runtime_policy),
            approval_policy=dict(profile.approval_policy),
        )

    def review_update(
        self,
        profile: AgentProfile,
        values: Mapping[str, object],
    ) -> ResourceReviewResult | None:
        if not AGENT_PROFILE_REVIEW_FIELDS.intersection(values):
            return None
        return self.review_profile(profile)

    def request_review(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        profile: AgentProfile,
        review: ResourceReviewResult,
    ) -> None:
        ResourceReviewApprovalService(self._session).request_resource_review(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            approval_type=REVIEW_TYPE_AGENT_PROFILE,
            target_type="agent_profile",
            target_id=profile.id,
            target_name=profile.name,
            review=review,
            snapshot=profile_snapshot(profile),
        )

    def _resource_reviews(self) -> ResourcePolicyReviewService:
        return ResourcePolicyReviewService(self._session, self._settings)
