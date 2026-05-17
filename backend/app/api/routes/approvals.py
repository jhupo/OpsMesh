from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.approvals import ApprovalDecisionRequest, ApprovalResponse
from backend.app.approvals.service import ApprovalService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/approvals", tags=["approvals"])


@router.get("", response_model=PageResponse[ApprovalResponse])
async def list_approvals(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
) -> PageResponse[ApprovalResponse]:
    items, total = ApprovalService(session).list_approvals(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
) -> ApprovalResponse:
    service = ApprovalService(session)
    approval = service.get_scoped(context.workspace.id, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return ApprovalResponse.model_validate(
        service.approve(approval, context.user.user_id, request.reason)
    )


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
) -> ApprovalResponse:
    service = ApprovalService(session)
    approval = service.get_scoped(context.workspace.id, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return ApprovalResponse.model_validate(
        service.reject(approval, context.user.user_id, request.reason)
    )
