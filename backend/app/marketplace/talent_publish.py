from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.marketplace import TalentListingCreateRequest
from backend.app.observability.audit_service import AuditService
from backend.app.core.config import Settings
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.marketplace.listing_payloads import (
    AGENT_SNAPSHOT_METADATA_KEY,
    agent_marketplace_snapshot,
)
from backend.app.marketplace.models import TalentListing
from backend.app.marketplace.talent_repository import TalentMarketplaceRepository
from backend.app.reviews.approval_service import ResourceReviewApprovalService
from backend.app.reviews.constants import (
    RESOURCE_STATUS_PENDING_APPROVAL,
    REVIEW_TYPE_AGENT_PROFILE,
)
from backend.app.reviews.service import ResourcePolicyReviewBuilder


class TalentPublishService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings
        self._repository = TalentMarketplaceRepository(session)

    def publish_agent(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        data: TalentListingCreateRequest,
    ) -> TalentListing:
        agent = self._repository.require_agent(workspace_id, data.agent_profile_id)
        metadata = dict(data.metadata)
        metadata[AGENT_SNAPSHOT_METADATA_KEY] = asdict(agent_marketplace_snapshot(agent))
        review = ResourcePolicyReviewBuilder(self._session, self._settings).review_agent_profile(
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
            ResourceReviewApprovalService(self._session).request_resource_review(
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
                "talent_listing.review_requested" if review.required else "talent_listing.published"
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
