from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.marketplace import (
    TalentListingMetricsResponse,
    TalentListingResponse,
    TalentListingReviewResponse,
)
from backend.app.db.session import get_db_session
from backend.app.marketplace.responses import review_response
from backend.app.marketplace.talent_catalog import TalentCatalogService
from backend.app.marketplace.talent_reviews import TalentReviewService

router = APIRouter()


@router.get("/talent-market", response_model=PageResponse[TalentListingResponse])
async def list_talent_market(
    page: PageParams = Depends(pagination_params),
    query: str | None = Query(default=None),
    role: str | None = Query(default=None),
    skill: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[TalentListingResponse]:
    items, total = TalentCatalogService(session).list_public_listings(
        page,
        query=query,
        role=role,
        skill=skill,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/talent-market/{listing_id}/metrics",
    response_model=TalentListingMetricsResponse,
)
async def get_talent_listing_metrics(
    listing_id: UUID,
    session: Session = Depends(get_db_session),
) -> TalentListingMetricsResponse:
    response = TalentReviewService(session).listing_metrics(listing_id)
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Talent listing not found",
        )
    return response


@router.get(
    "/talent-market/{listing_id}/reviews",
    response_model=PageResponse[TalentListingReviewResponse],
)
async def list_talent_listing_reviews(
    listing_id: UUID,
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[TalentListingReviewResponse]:
    result = TalentReviewService(session).list_reviews(listing_id, page)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Talent listing not found",
        )
    items, total = result
    return PageResponse(
        items=[review_response(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
