from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.notifications import (
    NotificationCountsResponse,
    NotificationMarkReadRequest,
    NotificationMarkReadResponse,
    NotificationResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session
from backend.app.notifications.service import NotificationCenterService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/notifications",
    tags=["notifications"],
)


@router.get("", response_model=PageResponse[NotificationResponse])
async def list_notifications(
    page: PageParams = Depends(pagination_params),
    include_archived: bool = Query(default=False),
    read: bool | None = Query(default=None),
    severity: str | None = Query(default=None),
    notification_type: str | None = Query(default=None, alias="type"),
    source_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[NotificationResponse]:
    items, total = NotificationCenterService(session).list_notifications(
        workspace_id=context.workspace.id,
        page=page,
        include_archived=include_archived,
        read=read,
        severity=severity,
        notification_type=notification_type,
        source_type=source_type,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/counts", response_model=NotificationCountsResponse)
async def get_notification_counts(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> NotificationCountsResponse:
    counts = NotificationCenterService(session).counts(context.workspace.id)
    return NotificationCountsResponse.model_validate(counts)


@router.post("/mark-read", response_model=NotificationMarkReadResponse)
async def mark_notifications_read(
    request: NotificationMarkReadRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> NotificationMarkReadResponse:
    updated_count = NotificationCenterService(session).mark_matching_read(
        context.workspace.id,
        notification_ids=request.notification_ids,
        include_archived=request.include_archived,
    )
    return NotificationMarkReadResponse(
        workspace_id=context.workspace.id,
        updated_count=updated_count,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> NotificationResponse:
    try:
        notification = NotificationCenterService(session).mark_read(
            context.workspace.id,
            notification_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NotificationResponse.model_validate(notification)


@router.post("/{notification_id}/archive", response_model=NotificationResponse)
async def archive_notification(
    notification_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> NotificationResponse:
    try:
        notification = NotificationCenterService(session).archive(
            context.workspace.id,
            notification_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NotificationResponse.model_validate(notification)
