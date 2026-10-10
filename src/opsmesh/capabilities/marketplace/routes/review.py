from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.capabilities.marketplace.contracts import (
    TalentListingReviewCreateRequest,
    TalentListingReviewResponse,
)
from opsmesh.capabilities.marketplace.responses import review_response
from opsmesh.capabilities.marketplace.talent_reviews import TalentReviewService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.db.errors import DatabaseConflictError
from opsmesh.shared.db.session import get_db_session

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/talent-market/{listing_id}/reviews",
    response_model=TalentListingReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def review_talent_listing(
    listing_id: UUID,
    request: TalentListingReviewCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TalentListingReviewResponse:
    try:
        review = TalentReviewService(session).upsert_review(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            listing_id=listing_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return review_response(review)
