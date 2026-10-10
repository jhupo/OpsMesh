from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.messaging.notifications.models import PlatformAnnouncement
from opsmesh.platform.announcements.schemas import (
    AdminAnnouncementCreateRequest,
    AdminAnnouncementResponse,
)
from opsmesh.platform.announcements.service import (
    AdminAnnouncementService,
    AnnouncementAudience,
)
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


def _response(
    service: AdminAnnouncementService,
    announcement: PlatformAnnouncement,
) -> AdminAnnouncementResponse:
    total, read, workspace_ids = service.stats(announcement.id)
    return AdminAnnouncementResponse(
        id=announcement.id,
        created_at=announcement.created_at,
        updated_at=announcement.updated_at,
        created_by_user_id=announcement.created_by_user_id,
        title=announcement.title,
        body=announcement.body,
        severity=announcement.severity,
        status=announcement.status,
        audience=announcement.audience,
        published_at=announcement.published_at,
        retracted_at=announcement.retracted_at,
        recipient_count=total,
        read_count=read,
        workspace_ids=workspace_ids,
    )


@router.post(
    "/announcements",
    response_model=AdminAnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
)
def publish_admin_announcement(
    request: AdminAnnouncementCreateRequest,
    session: Session = Depends(get_db_session),
) -> AdminAnnouncementResponse:
    service = AdminAnnouncementService(session)
    try:
        announcement = service.publish(
            title=request.title,
            body=request.body,
            severity=request.severity,
            audience=AnnouncementAudience(
                workspace_ids=tuple(request.workspace_ids),
                roles=tuple(request.roles),
                user_ids=tuple(request.user_ids),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _response(service, announcement)


@router.get(
    "/announcements",
    response_model=PageResponse[AdminAnnouncementResponse],
)
def list_admin_announcements(
    page: PageParams = Depends(pagination_params),
    announcement_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminAnnouncementResponse]:
    service = AdminAnnouncementService(session)
    announcements, total = service.list_announcements(page, status=announcement_status)
    return PageResponse(
        items=[_response(service, announcement) for announcement in announcements],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/announcements/{announcement_id}",
    response_model=AdminAnnouncementResponse,
)
def get_admin_announcement(
    announcement_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminAnnouncementResponse:
    service = AdminAnnouncementService(session)
    announcement = service.get_announcement(announcement_id)
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    return _response(service, announcement)


@router.post(
    "/announcements/{announcement_id}/retract",
    response_model=AdminAnnouncementResponse,
)
def retract_admin_announcement(
    announcement_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminAnnouncementResponse:
    service = AdminAnnouncementService(session)
    try:
        announcement = service.retract(announcement_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    return _response(service, announcement)
