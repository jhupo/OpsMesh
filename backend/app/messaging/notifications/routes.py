from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.identity.auth.dependencies import workspace_dependency
from backend.app.identity.authorization.context import WorkspaceContext
from backend.app.identity.authorization.permissions import WorkspaceAction
from backend.app.messaging.notifications.contracts import (
    NotificationCountsResponse,
    NotificationMarkReadRequest,
    NotificationMarkReadResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdateRequest,
    NotificationResponse,
)
from backend.app.messaging.notifications.service import NotificationCenterService
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.pagination import PageParams

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
        user_id=context.user.user_id,
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
    counts = NotificationCenterService(session).counts(
        context.workspace.id,
        user_id=context.user.user_id,
    )
    return NotificationCountsResponse.model_validate(counts)


@router.post("/mark-read", response_model=NotificationMarkReadResponse)
async def mark_notifications_read(
    request: NotificationMarkReadRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> NotificationMarkReadResponse:
    updated_count = NotificationCenterService(session).mark_matching_read(
        context.workspace.id,
        user_id=context.user.user_id,
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
            user_id=context.user.user_id,
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
            user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NotificationResponse.model_validate(notification)


@router.get("/preferences", response_model=NotificationPreferenceResponse)
async def get_notification_preferences(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> NotificationPreferenceResponse:
    preference = NotificationCenterService(session).get_preferences(
        context.workspace.id,
        context.user.user_id,
    )
    if preference is None:
        return NotificationPreferenceResponse(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            in_app_enabled=True,
            email_enabled=True,
            announcement_enabled=True,
            task_enabled=True,
            approval_enabled=True,
            security_enabled=True,
        )
    return NotificationPreferenceResponse.model_validate(preference)


@router.put("/preferences", response_model=NotificationPreferenceResponse)
async def update_notification_preferences(
    request: NotificationPreferenceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> NotificationPreferenceResponse:
    preference = NotificationCenterService(session).upsert_preferences(
        context.workspace.id,
        context.user.user_id,
        request.model_dump(exclude_unset=True),
    )
    return NotificationPreferenceResponse.model_validate(preference)
