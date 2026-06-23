from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.notifications import NotificationCreateRequest
from backend.app.db.pagination import page_scalars
from backend.app.notifications.models import WorkspaceNotification


class NotificationCenterService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        workspace_id: UUID,
        data: NotificationCreateRequest,
    ) -> WorkspaceNotification:
        notification = WorkspaceNotification(
            workspace_id=workspace_id,
            notification_type=data.notification_type,
            severity=data.severity,
            source_type=data.source_type,
            source_id=data.source_id,
            title=data.title,
            body=data.body,
            metadata_=data.metadata,
        )
        self._session.add(notification)
        self._session.commit()
        self._session.refresh(notification)
        return notification

    def list_notifications(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        include_archived: bool = False,
        read: bool | None = None,
        severity: str | None = None,
        notification_type: str | None = None,
        source_type: str | None = None,
    ) -> tuple[list[WorkspaceNotification], int]:
        statement = self._filtered_statement(
            workspace_id,
            include_archived=include_archived,
            read=read,
            severity=severity,
            notification_type=notification_type,
            source_type=source_type,
        ).order_by(WorkspaceNotification.created_at.desc(), WorkspaceNotification.id.desc())
        return self._page(statement, page)

    def counts(self, workspace_id: UUID) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "total_count": self._count(workspace_id),
            "unread_count": self._count(workspace_id, read=False, include_archived=False),
            "read_count": self._count(workspace_id, read=True, include_archived=False),
            "archived_count": self._count_archived(workspace_id),
            "severity_counts": self._group_counts(workspace_id, WorkspaceNotification.severity),
            "type_counts": self._group_counts(
                workspace_id,
                WorkspaceNotification.notification_type,
            ),
            "source_type_counts": self._group_counts(
                workspace_id,
                WorkspaceNotification.source_type,
            ),
        }

    def mark_read(self, workspace_id: UUID, notification_id: UUID) -> WorkspaceNotification:
        notification = self._require_notification(workspace_id, notification_id)
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            self._session.commit()
            self._session.refresh(notification)
        return notification

    def archive(self, workspace_id: UUID, notification_id: UUID) -> WorkspaceNotification:
        notification = self._require_notification(workspace_id, notification_id)
        now = datetime.now(UTC)
        if notification.read_at is None:
            notification.read_at = now
        if notification.archived_at is None:
            notification.archived_at = now
        self._session.commit()
        self._session.refresh(notification)
        return notification

    def mark_matching_read(
        self,
        workspace_id: UUID,
        *,
        notification_ids: list[UUID] | None = None,
        include_archived: bool = False,
    ) -> int:
        statement = select(WorkspaceNotification).where(
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.read_at.is_(None),
        )
        if not include_archived:
            statement = statement.where(WorkspaceNotification.archived_at.is_(None))
        if notification_ids is not None:
            statement = statement.where(WorkspaceNotification.id.in_(notification_ids))

        notifications = list(self._session.scalars(statement))
        now = datetime.now(UTC)
        for notification in notifications:
            notification.read_at = now
        if notifications:
            self._session.commit()
        return len(notifications)

    def _filtered_statement(
        self,
        workspace_id: UUID,
        *,
        include_archived: bool,
        read: bool | None,
        severity: str | None,
        notification_type: str | None,
        source_type: str | None,
    ) -> Select[tuple[WorkspaceNotification]]:
        statement = select(WorkspaceNotification).where(
            WorkspaceNotification.workspace_id == workspace_id,
        )
        if not include_archived:
            statement = statement.where(WorkspaceNotification.archived_at.is_(None))
        if read is True:
            statement = statement.where(WorkspaceNotification.read_at.is_not(None))
        if read is False:
            statement = statement.where(WorkspaceNotification.read_at.is_(None))
        if severity is not None:
            statement = statement.where(WorkspaceNotification.severity == severity)
        if notification_type is not None:
            statement = statement.where(
                WorkspaceNotification.notification_type == notification_type,
            )
        if source_type is not None:
            statement = statement.where(WorkspaceNotification.source_type == source_type)
        return statement

    def _require_notification(
        self,
        workspace_id: UUID,
        notification_id: UUID,
    ) -> WorkspaceNotification:
        notification = self._session.scalar(
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.id == notification_id,
            )
        )
        if notification is None:
            raise ValueError("Notification not found")
        return notification

    def _page(
        self,
        statement: Select[tuple[WorkspaceNotification]],
        page: PageParams,
    ) -> tuple[list[WorkspaceNotification], int]:
        return page_scalars(self._session, statement, page)

    def _count(
        self,
        workspace_id: UUID,
        *,
        read: bool | None = None,
        include_archived: bool = True,
    ) -> int:
        statement = select(func.count()).where(WorkspaceNotification.workspace_id == workspace_id)
        if not include_archived:
            statement = statement.where(WorkspaceNotification.archived_at.is_(None))
        if read is True:
            statement = statement.where(WorkspaceNotification.read_at.is_not(None))
        if read is False:
            statement = statement.where(WorkspaceNotification.read_at.is_(None))
        total = self._session.scalar(statement)
        return int(total or 0)

    def _count_archived(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.archived_at.is_not(None),
            )
        )
        return int(total or 0)

    def _group_counts(self, workspace_id: UUID, column: object) -> dict[str, int]:
        rows = self._session.execute(
            select(column, func.count())
            .where(WorkspaceNotification.workspace_id == workspace_id)
            .group_by(column)
        )
        return {str(key): int(count) for key, count in rows}
