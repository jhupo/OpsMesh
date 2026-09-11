from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.marketplace import (
    TalentListingMetricsResponse,
    TalentListingReviewCreateRequest,
)
from backend.app.observability.audit_service import AuditService
from backend.app.core.pagination import PageParams
from backend.app.db.errors import commit_or_raise_conflict
from backend.app.db.pagination import page_scalars
from backend.app.marketplace.models import TalentListingReview
from backend.app.marketplace.talent_repository import TalentMarketplaceRepository


class TalentReviewService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = TalentMarketplaceRepository(session)

    def upsert_review(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        listing_id: UUID,
        data: TalentListingReviewCreateRequest,
    ) -> TalentListingReview:
        listing = self._repository.get_public_listing(listing_id)
        if listing is None:
            raise ValueError("Talent listing not found")
        install = self._repository.review_install(
            workspace_id,
            listing_id,
            data.workspace_agent_install_id,
        )
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
        if self._repository.get_public_listing(listing_id) is None:
            return None
        statement = (
            select(TalentListingReview)
            .where(
                TalentListingReview.talent_listing_id == listing_id,
                TalentListingReview.status == "active",
            )
            .order_by(TalentListingReview.created_at.desc())
        )
        return page_scalars(self._session, statement, page)

    def listing_metrics(self, listing_id: UUID) -> TalentListingMetricsResponse | None:
        listing = self._repository.get_public_listing(listing_id)
        if listing is None:
            return None
        return TalentListingMetricsResponse(
            talent_listing_id=listing.id,
            install_count=listing.install_count,
            upgrade_count=listing.upgrade_count,
            review_count=listing.review_count,
            average_rating=listing.average_rating,
        )

