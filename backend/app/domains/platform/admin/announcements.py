from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.core.security.redaction import redact_sensitive_text
from backend.app.domains.access.models import User
from backend.app.domains.workspace.tenants.models import Workspace, WorkspaceMember
from backend.app.observability.audit.service import AuditService
from backend.app.observability.notifications.contracts import NotificationCreateRequest
from backend.app.observability.notifications.models import (
    PlatformAnnouncement,
    PlatformAnnouncementRecipient,
    UserNotificationPreference,
    WorkspaceNotification,
)
from backend.app.observability.notifications.service import NotificationCenterService


@dataclass(frozen=True)
class AnnouncementAudience:
    workspace_ids: tuple[UUID, ...] = ()
    roles: tuple[str, ...] = ()
    user_ids: tuple[UUID, ...] = ()

    def as_payload(self) -> dict[str, object]:
        return {
            "workspace_ids": [str(item) for item in self.workspace_ids],
            "roles": list(self.roles),
            "user_ids": [str(item) for item in self.user_ids],
        }


class AdminAnnouncementService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._notifications = NotificationCenterService(session)

    def publish(
        self,
        *,
        title: str,
        body: str,
        severity: str,
        audience: AnnouncementAudience,
        created_by_user_id: UUID | None = None,
    ) -> PlatformAnnouncement:
        members = list(self._session.scalars(self._recipient_statement(audience)))
        if not members:
            raise ValueError("Announcement audience has no active members")
        now = datetime.now(UTC)
        announcement = PlatformAnnouncement(
            created_by_user_id=created_by_user_id,
            title=redact_sensitive_text(title),
            body=redact_sensitive_text(body),
            severity=severity,
            status="published",
            audience=audience.as_payload(),
            published_at=now,
        )
        self._session.add(announcement)
        self._session.flush()
        target_workspace_ids = {member.workspace_id for member in members}
        delivered_by_workspace: dict[UUID, int] = {}
        for member in members:
            preference = self._session.scalar(
                select(UserNotificationPreference).where(
                    UserNotificationPreference.workspace_id == member.workspace_id,
                    UserNotificationPreference.user_id == member.user_id,
                )
            )
            if preference is not None and (
                not preference.in_app_enabled or not preference.announcement_enabled
            ):
                continue
            notification = self._notifications.create(
                member.workspace_id,
                NotificationCreateRequest(
                    notification_type="announcement",
                    severity=severity,
                    source_type="platform_announcement",
                    source_id=announcement.id,
                    title=title,
                    body=body,
                    metadata={"announcement_id": str(announcement.id)},
                ),
                recipient_user_id=member.user_id,
            )
            self._session.add(
                PlatformAnnouncementRecipient(
                    announcement_id=announcement.id,
                    workspace_id=member.workspace_id,
                    user_id=member.user_id,
                    notification_id=notification.id,
                    delivered_at=now,
                )
            )
            delivered_by_workspace[member.workspace_id] = (
                delivered_by_workspace.get(member.workspace_id, 0) + 1
            )
        for workspace_id in sorted(target_workspace_ids, key=str):
            AuditService(self._session).record_system_action(
                workspace_id=workspace_id,
                action="platform.announcement.published",
                target_type="platform_announcement",
                target_id=announcement.id,
                metadata={
                    "severity": severity,
                    "recipient_count": delivered_by_workspace.get(workspace_id, 0),
                    "audience": audience.as_payload(),
                },
                actor_id="platform_admin",
            )
        self._session.commit()
        self._session.refresh(announcement)
        return announcement

    def list_announcements(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[PlatformAnnouncement], int]:
        statement = select(PlatformAnnouncement)
        count_statement = select(func.count()).select_from(PlatformAnnouncement)
        if status is not None:
            statement = statement.where(PlatformAnnouncement.status == status)
            count_statement = count_statement.where(PlatformAnnouncement.status == status)
        total = int(self._session.scalar(count_statement) or 0)
        items, _ = page_scalars(
            self._session,
            statement.order_by(
                PlatformAnnouncement.created_at.desc(),
                PlatformAnnouncement.id.desc(),
            ),
            page,
        )
        return items, total

    def get_announcement(self, announcement_id: UUID) -> PlatformAnnouncement | None:
        return self._session.get(PlatformAnnouncement, announcement_id)

    def retract(self, announcement_id: UUID) -> PlatformAnnouncement | None:
        announcement = self._session.scalar(
            select(PlatformAnnouncement)
            .where(PlatformAnnouncement.id == announcement_id)
            .with_for_update()
        )
        if announcement is None:
            return None
        if announcement.status == "retracted":
            return announcement
        if announcement.status != "published":
            raise ValueError("Only published announcements can be retracted")
        now = datetime.now(UTC)
        announcement.status = "retracted"
        announcement.retracted_at = now
        recipients = list(
            self._session.scalars(
                select(PlatformAnnouncementRecipient).where(
                    PlatformAnnouncementRecipient.announcement_id == announcement.id
                )
            )
        )
        workspace_ids: set[UUID] = set()
        for recipient in recipients:
            workspace_ids.add(recipient.workspace_id)
            if recipient.notification_id is None:
                continue
            notification = self._session.get(WorkspaceNotification, recipient.notification_id)
            if notification is not None and notification.archived_at is None:
                notification.archived_at = now
        for workspace_id in sorted(workspace_ids, key=str):
            AuditService(self._session).record_system_action(
                workspace_id=workspace_id,
                action="platform.announcement.retracted",
                target_type="platform_announcement",
                target_id=announcement.id,
                metadata={"recipient_count": len(recipients)},
                actor_id="platform_admin",
            )
        self._session.commit()
        self._session.refresh(announcement)
        return announcement

    def stats(self, announcement_id: UUID) -> tuple[int, int, list[UUID]]:
        total = int(
            self._session.scalar(
                select(func.count()).where(
                    PlatformAnnouncementRecipient.announcement_id == announcement_id
                )
            )
            or 0
        )
        read = int(
            self._session.scalar(
                select(func.count())
                .select_from(PlatformAnnouncementRecipient)
                .join(
                    WorkspaceNotification,
                    WorkspaceNotification.id == PlatformAnnouncementRecipient.notification_id,
                )
                .where(
                    PlatformAnnouncementRecipient.announcement_id == announcement_id,
                    WorkspaceNotification.read_at.is_not(None),
                )
            )
            or 0
        )
        workspace_ids = list(
            self._session.scalars(
                select(PlatformAnnouncementRecipient.workspace_id)
                .where(PlatformAnnouncementRecipient.announcement_id == announcement_id)
                .distinct()
                .order_by(PlatformAnnouncementRecipient.workspace_id)
            )
        )
        return total, read, workspace_ids

    def _recipient_statement(
        self,
        audience: AnnouncementAudience,
    ) -> Select[tuple[WorkspaceMember]]:
        conditions = []
        if audience.workspace_ids:
            conditions.append(WorkspaceMember.workspace_id.in_(audience.workspace_ids))
        if audience.roles:
            conditions.append(WorkspaceMember.role.in_(audience.roles))
        if audience.user_ids:
            conditions.append(WorkspaceMember.user_id.in_(audience.user_ids))
        if not conditions:
            raise ValueError("Announcement audience must select a workspace, role, or user")
        return (
            select(WorkspaceMember)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                Workspace.status == "active",
                WorkspaceMember.status == "active",
                User.status == "active",
                or_(*conditions),
            )
            .order_by(WorkspaceMember.workspace_id, WorkspaceMember.user_id)
        )
