from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.approvals import ApprovalDecisionRequest, ApprovalResponse
from backend.app.approvals.decisions import ApprovalDecisionService
from backend.app.approvals.queries import ApprovalQueryService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction, WorkspaceRole
from backend.app.db.session import get_db_session
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

router = APIRouter(prefix="/workspaces/{workspace_id}/approvals", tags=["approvals"])


@router.get("", response_model=PageResponse[ApprovalResponse])
async def list_approvals(
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
async def approve(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> ApprovalResponse:
    approval = ApprovalQueryService(session).get_scoped(context.workspace.id, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    _require_resource_review_admin(context, approval.payload)
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
async def reject(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.APPROVE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> ApprovalResponse:
    approval = ApprovalQueryService(session).get_scoped(context.workspace.id, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    _require_resource_review_admin(context, approval.payload)
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


def _require_resource_review_admin(context: WorkspaceContext, payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("kind") != "resource_review":
        return
    if _can_review_resources(context):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Resource review approvals require a workspace admin",
    )


def _can_review_resources(context: WorkspaceContext) -> bool:
    return context.role in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}
