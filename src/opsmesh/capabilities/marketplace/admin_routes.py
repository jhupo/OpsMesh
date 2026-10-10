from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from opsmesh.capabilities.marketplace.admin_reviews import AdminMarketplaceReviewService
from opsmesh.identity.authorization.admin_actor import require_admin_actor
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.orchestration.approvals.schemas import ApprovalResponse
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


class PublicationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=500)


@router.get("/marketplace/reviews", response_model=PageResponse[ApprovalResponse])
def list_reviews(
    workspace_id: UUID | None = None,
    status: Literal["pending", "approved", "rejected"] | None = "pending",
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[ApprovalResponse]:
    rows, total = AdminMarketplaceReviewService(session).list_reviews(
        page,
        workspace_id=workspace_id,
        status=status,
    )
    return PageResponse(
        items=[ApprovalResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/workspaces/{workspace_id}/marketplace/reviews/{approval_id}/decision",
    response_model=ApprovalResponse,
)
def decide_review(
    workspace_id: UUID,
    approval_id: UUID,
    body: PublicationDecisionRequest,
    actor: AuthenticatedUser = Depends(require_admin_actor),
    session: Session = Depends(get_db_session),
) -> ApprovalResponse:
    return ApprovalResponse.model_validate(
        AdminMarketplaceReviewService(session).decide(
            workspace_id,
            approval_id,
            actor=actor,
            approved=body.decision == "approved",
            reason=body.reason,
        )
    )
