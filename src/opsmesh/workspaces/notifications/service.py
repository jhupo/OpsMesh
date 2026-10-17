"""Notification service: deduplicated creation and per-user receipts.

Creation validates that the recipient is a member of the workspace the
notification is addressed in, then writes one row per source. Marking
read is idempotent and only ever touches the current user's own rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.members import access
from opsmesh.workspaces.notifications.models import (
    NOTIFICATION_SEVERITIES,
    NotificationPreference,
    WorkspaceNotification,
)


def record_notification(
    session: Session,
    *,
    actor: AuditActor,
    workspace_id: UUID,
    recipient_user_id: UUID,
    notification_type: str,
    severity: str,
    source_type: str,
    source_id: UUID | None,
    title: str,
    body: str,
    payload: dict[str, object],
) -> WorkspaceNotification:
    """Write one notification; the same source replays the same row.

    The unique source constraint is the deduplication contract: a producer
    that retries its source reference reuses the existing inbox row instead
    of notifying the member twice. The contract holds under concurrency as
    well — the producer that loses the insert race reads back the row the
    winner committed and replays it.
    """
    if severity not in NOTIFICATION_SEVERITIES:
        raise InvalidArgumentError(
            "The notification severity is unknown",
            details={"severity": severity, "known_severities": list(NOTIFICATION_SEVERITIES)},
        )
    if access.space_role(session, workspace_id=workspace_id, user_id=recipient_user_id) is None:
        raise InvalidArgumentError("The recipient is not a workspace member")
    existing = session.scalar(
        _source_statement(
            workspace_id=workspace_id,
            recipient_user_id=recipient_user_id,
            notification_type=notification_type,
            source_type=source_type,
            source_id=source_id,
        )
    )
    if existing is not None:
        return existing
    notification = WorkspaceNotification(
        workspace_id=workspace_id,
        recipient_user_id=recipient_user_id,
        notification_type=notification_type,
        severity=severity,
        source_type=source_type,
        source_id=source_id,
        title=title,
        body=body,
        payload=payload,
    )
    try:
        # The savepoint keeps this transaction usable when a concurrent
        # producer of the same source lands the row first: the constraint
        # refuses the insert, the savepoint rolls back only that insert
        # (the pending object is expunged with it), and the committed row
        # is read back and replayed instead of failing the retry.
        with session.begin_nested():
            session.add(notification)
            session.flush()
    except IntegrityError as exc:
        replayed = session.scalar(
            _source_statement(
                workspace_id=workspace_id,
                recipient_user_id=recipient_user_id,
                notification_type=notification_type,
                source_type=source_type,
                source_id=source_id,
            )
        )
        if replayed is None:
            raise ConflictError(
                "The notification source already exists",
                details={"source_type": source_type, "source_id": str(source_id)},
            ) from exc
        return replayed
    append_platform_event(
        session,
        actor=actor,
        action="workspace.notification_created",
        target_type="workspace_notification",
        target_id=str(notification.id),
        summary="Workspace notification created",
        details={"notification_type": notification_type, "severity": severity},
    )
    return notification


def list_notifications(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    page: PageParams,
    include_archived: bool = False,
    unread_only: bool = False,
) -> tuple[list[WorkspaceNotification], int, int]:
    """One page of the current user's inbox plus the unread count."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    statement = select(WorkspaceNotification).where(
        WorkspaceNotification.workspace_id == workspace_id,
        WorkspaceNotification.recipient_user_id == actor.user_id,
    )
    if not include_archived:
        statement = statement.where(WorkspaceNotification.archived_at.is_(None))
    if unread_only:
        statement = statement.where(WorkspaceNotification.read_at.is_(None))
    statement = statement.order_by(
        WorkspaceNotification.created_at.desc(), WorkspaceNotification.id
    )
    rows, total = page_scalars(session, statement, page)
    unread_count = _unread_count(session, workspace_id=workspace_id, user_id=actor.user_id)
    return rows, total, unread_count


def mark_read(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    notification_ids: list[UUID],
    mark_all: bool,
) -> int:
    """Mark the current user's own notifications read; repeats change nothing."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    if not mark_all and not notification_ids:
        raise InvalidArgumentError("Name the notifications to mark read, or mark all")
    unread = select(WorkspaceNotification.id).where(
        WorkspaceNotification.workspace_id == workspace_id,
        WorkspaceNotification.recipient_user_id == actor.user_id,
        WorkspaceNotification.read_at.is_(None),
    )
    if not mark_all:
        unread = unread.where(WorkspaceNotification.id.in_(notification_ids))
    targets = list(session.scalars(unread))
    if not targets:
        return 0
    session.execute(
        update(WorkspaceNotification)
        .where(WorkspaceNotification.id.in_(targets))
        .values(read_at=datetime.now(UTC))
    )
    return len(targets)


def archive_notification(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, notification_id: UUID
) -> WorkspaceNotification:
    """Archive one of the current user's own notifications."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    notification = session.scalar(
        select(WorkspaceNotification).where(
            WorkspaceNotification.id == notification_id,
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.recipient_user_id == actor.user_id,
        )
    )
    if notification is None:
        raise InvalidArgumentError("The notification does not exist in this inbox")
    now = datetime.now(UTC)
    if notification.read_at is None:
        notification.read_at = now
    notification.archived_at = now
    return notification


def read_preferences(
    session: Session, *, actor: CurrentUser, workspace_id: UUID
) -> NotificationPreference:
    """The current user's preferences in one workspace, created on first read."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    preference = session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.workspace_id == workspace_id,
            NotificationPreference.user_id == actor.user_id,
        )
    )
    if preference is None:
        preference = NotificationPreference(
            workspace_id=workspace_id, user_id=actor.user_id
        )
        session.add(preference)
        session.flush()
    return preference


def update_preferences(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    in_app_enabled: bool | None,
    email_enabled: bool | None,
) -> NotificationPreference:
    """Change the current user's preferences; unset fields keep their value."""
    preference = read_preferences(session, actor=actor, workspace_id=workspace_id)
    if in_app_enabled is not None:
        preference.in_app_enabled = in_app_enabled
    if email_enabled is not None:
        preference.email_enabled = email_enabled
    return preference


def _source_statement(
    *,
    workspace_id: UUID,
    recipient_user_id: UUID,
    notification_type: str,
    source_type: str,
    source_id: UUID | None,
) -> Select[tuple[WorkspaceNotification]]:
    """The one row identity a source reference maps to.

    The pre-insert lookup and the post-conflict replay must select the same
    row the unique constraint protects, so both share this statement.
    """
    return select(WorkspaceNotification).where(
        WorkspaceNotification.workspace_id == workspace_id,
        WorkspaceNotification.recipient_user_id == recipient_user_id,
        WorkspaceNotification.source_type == source_type,
        WorkspaceNotification.source_id == source_id,
        WorkspaceNotification.notification_type == notification_type,
    )


def _unread_count(session: Session, *, workspace_id: UUID, user_id: UUID) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(WorkspaceNotification)
        .where(
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.recipient_user_id == user_id,
            WorkspaceNotification.read_at.is_(None),
            WorkspaceNotification.archived_at.is_(None),
        )
    )
    return int(count or 0)


__all__ = [
    "archive_notification",
    "list_notifications",
    "mark_read",
    "read_preferences",
    "record_notification",
    "update_preferences",
]
