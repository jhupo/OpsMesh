"""Workspace notification inbox APIs: list, unread count, batch read, archive,
preferences.

Only the recipient's own inbox is reachable here; there is no creation
route, because producers are platform components and later steps, not
members posting to each other.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.workspaces.notifications import service
from opsmesh.workspaces.notifications.models import (
    NotificationPreference,
    WorkspaceNotification,
)
from opsmesh.workspaces.notifications.schemas import (
    MarkReadRequest,
    MarkReadView,
    NotificationPage,
    NotificationPreferenceUpdateRequest,
    NotificationPreferenceView,
    NotificationView,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/notifications", tags=["workspaces"]
)

WorkspaceIdPath = Annotated[UUID, Path()]
NotificationIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _view(notification: WorkspaceNotification) -> NotificationView:
    return NotificationView(
        notification_id=notification.id,
        workspace_id=notification.workspace_id,
        recipient_user_id=notification.recipient_user_id,
        notification_type=notification.notification_type,
        severity=notification.severity,
        source_type=notification.source_type,
        source_id=notification.source_id,
        title=notification.title,
        body=notification.body,
        payload=dict(notification.payload),
        read_at=notification.read_at,
        archived_at=notification.archived_at,
        created_at=notification.created_at,
    )


def _preference_view(preference: NotificationPreference) -> NotificationPreferenceView:
    return NotificationPreferenceView(
        workspace_id=preference.workspace_id,
        user_id=preference.user_id,
        in_app_enabled=preference.in_app_enabled,
        email_enabled=preference.email_enabled,
    )


@router.get("", response_model=NotificationPage, operation_id="workspaces.notifications.list")
def list_notifications(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
    include_archived: Annotated[bool, Query()] = False,
    unread_only: Annotated[bool, Query()] = False,
) -> NotificationPage:
    """List the current user's inbox in one workspace."""
    rows, total, unread_count = service.list_notifications(
        session,
        actor=user,
        workspace_id=workspace_id,
        page=PageParams(limit=limit, offset=offset),
        include_archived=include_archived,
        unread_only=unread_only,
    )
    return NotificationPage(
        notifications=[_view(item) for item in rows], total=total, unread_count=unread_count
    )


@router.post(
    "/read",
    response_model=MarkReadView,
    operation_id="workspaces.notifications.mark_read",
)
def mark_notifications_read(
    payload: MarkReadRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
) -> MarkReadView:
    """Mark named notifications, or the whole inbox, read."""
    with session_transaction(session):
        marked = service.mark_read(
            session,
            actor=user,
            workspace_id=workspace_id,
            notification_ids=payload.notification_ids,
            mark_all=payload.mark_all,
        )
    return MarkReadView(marked_count=marked)


@router.post(
    "/{notification_id}/archive",
    response_model=NotificationView,
    operation_id="workspaces.notifications.archive",
)
def archive_notification(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    notification_id: NotificationIdPath,
) -> NotificationView:
    """Archive one of the current user's own notifications."""
    with session_transaction(session):
        notification = service.archive_notification(
            session, actor=user, workspace_id=workspace_id, notification_id=notification_id
        )
    return _view(notification)


@router.get(
    "/preferences",
    response_model=NotificationPreferenceView,
    operation_id="workspaces.notifications.read_preferences",
)
def read_preferences(
    session: DbSession, user: CurrentUserDep, workspace_id: WorkspaceIdPath
) -> NotificationPreferenceView:
    """Read the current user's notification preferences in one workspace."""
    return _preference_view(
        service.read_preferences(session, actor=user, workspace_id=workspace_id)
    )


@router.put(
    "/preferences",
    response_model=NotificationPreferenceView,
    operation_id="workspaces.notifications.update_preferences",
)
def update_preferences(
    payload: NotificationPreferenceUpdateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
) -> NotificationPreferenceView:
    """Change the current user's notification preferences."""
    with session_transaction(session):
        preference = service.update_preferences(
            session,
            actor=user,
            workspace_id=workspace_id,
            in_app_enabled=payload.in_app_enabled,
            email_enabled=payload.email_enabled,
        )
    return _preference_view(preference)
