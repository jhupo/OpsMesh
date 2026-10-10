from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction, WorkspaceRole
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.queries import ApprovalQueryService
from opsmesh.orchestration.approvals.schemas import ApprovalDecisionRequest, ApprovalResponse
from opsmesh.runtime.queues.dependencies import get_worker_queue
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter(prefix="/workspaces/{workspace_id}/approvals", tags=["approvals"])


@router.get("", response_model=PageResponse[ApprovalResponse])
def list_approvals(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
) -> PageResponse[ApprovalResponse]:
    items, total = ApprovalQueryService(session).list_approvals(
        context.workspace.id,
        page,
        status_filter,
        include_resource_reviews=_can_review_resources(context),
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
def approve(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> ApprovalResponse:
    approval = ApprovalQueryService(session).get_scoped(context.workspace.id, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    ApprovalDecisionService(session, queue).require_actor(approval, context.user)
    try:
        return ApprovalResponse.model_validate(
            ApprovalDecisionService(session, queue).approve(
                approval,
                context.user.user_id,
                request.reason,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
def reject(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> ApprovalResponse:
    approval = ApprovalQueryService(session).get_scoped(context.workspace.id, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    ApprovalDecisionService(session, queue).require_actor(approval, context.user)
    try:
        return ApprovalResponse.model_validate(
            ApprovalDecisionService(session, queue).reject(
                approval,
                context.user.user_id,
                request.reason,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _can_review_resources(context: WorkspaceContext) -> bool:
    return context.role in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}
