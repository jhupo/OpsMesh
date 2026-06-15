from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agents.model_provider_summary import agent_profile_response
from backend.app.api.schemas.marketplace import (
    MarketplaceListingResponse,
    TalentListingReviewResponse,
    WorkspaceAgentInstallResponse,
    WorkspaceMarketplaceInstallResponse,
)
from backend.app.marketplace.models import (
    TalentListingReview,
    WorkspaceAgentInstall,
    WorkspaceMarketplaceInstall,
)


def install_response(install: WorkspaceAgentInstall) -> WorkspaceAgentInstallResponse:
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


def review_response(review: TalentListingReview) -> TalentListingReviewResponse:
    return TalentListingReviewResponse.model_validate(review)
