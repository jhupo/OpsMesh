from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, false, func, not_, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql import ColumnElement

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.core.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.observability.audit.models import AuditIntegrityCheck
from backend.app.observability.costs.models import ModelUsageRecord
from backend.app.observability.notifications.contracts import NotificationCreateRequest
from backend.app.observability.notifications.models import (
    UserNotificationPreference,
    WorkspaceNotification,
)


class NotificationCenterService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        workspace_id: UUID,
        data: NotificationCreateRequest,
        *,
        recipient_user_id: UUID | None = None,
    ) -> WorkspaceNotification:
        notification = WorkspaceNotification(
            workspace_id=workspace_id,
            notification_type=data.notification_type,
            severity=data.severity,
            source_type=data.source_type,
            source_id=data.source_id,
            recipient_user_id=recipient_user_id,
            title=redact_sensitive_text(data.title),
            body=redact_sensitive_text(data.body),
            metadata_=redact_sensitive_payload(data.metadata),
        )
        self._session.add(notification)
        self._session.flush([notification])
        return notification

    def create_once(
        self,
        workspace_id: UUID,
        data: NotificationCreateRequest,
    ) -> WorkspaceNotification:
        existing = self._session.scalar(
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.notification_type == data.notification_type,
                WorkspaceNotification.source_type == data.source_type,
                WorkspaceNotification.source_id == data.source_id,
                WorkspaceNotification.read_at.is_(None),
                WorkspaceNotification.archived_at.is_(None),
            )
        )
        return existing if existing is not None else self.create(workspace_id, data)

    def list_notifications(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        user_id: UUID | None = None,
        include_archived: bool = False,
        read: bool | None = None,
        severity: str | None = None,
        notification_type: str | None = None,
        source_type: str | None = None,
    ) -> tuple[list[WorkspaceNotification], int]:
        statement = self._filtered_statement(
            workspace_id,
            user_id=user_id,
            include_archived=include_archived,
            read=read,
            severity=severity,
            notification_type=notification_type,
            source_type=source_type,
        ).order_by(WorkspaceNotification.created_at.desc(), WorkspaceNotification.id.desc())
        return self._page(statement, page)

    def counts(self, workspace_id: UUID, *, user_id: UUID | None = None) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "total_count": self._count(workspace_id, user_id=user_id),
            "unread_count": self._count(
                workspace_id,
                user_id=user_id,
                read=False,
                include_archived=False,
            ),
            "read_count": self._count(
                workspace_id,
                user_id=user_id,
                read=True,
                include_archived=False,
            ),
            "archived_count": self._count_archived(workspace_id, user_id=user_id),
            "severity_counts": self._group_counts(
                workspace_id,
                WorkspaceNotification.severity,
                user_id=user_id,
            ),
            "type_counts": self._group_counts(
                workspace_id,
                WorkspaceNotification.notification_type,
                user_id=user_id,
            ),
            "source_type_counts": self._group_counts(
                workspace_id,
                WorkspaceNotification.source_type,
                user_id=user_id,
            ),
        }

    def mark_read(
        self,
        workspace_id: UUID,
        notification_id: UUID,
        *,
        user_id: UUID | None = None,
    ) -> WorkspaceNotification:
        notification = self._require_notification(workspace_id, notification_id, user_id=user_id)
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            self._session.commit()
            self._session.refresh(notification)
        return notification

    def archive(
        self,
        workspace_id: UUID,
        notification_id: UUID,
        *,
        user_id: UUID | None = None,
    ) -> WorkspaceNotification:
        notification = self._require_notification(workspace_id, notification_id, user_id=user_id)
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
        user_id: UUID | None = None,
        notification_ids: list[UUID] | None = None,
        include_archived: bool = False,
    ) -> int:
        statement = select(WorkspaceNotification).where(
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.read_at.is_(None),
        )
        statement = statement.where(*self._preference_filters(workspace_id, user_id))
        if user_id is not None:
            statement = statement.where(
                or_(
                    WorkspaceNotification.recipient_user_id.is_(None),
                    WorkspaceNotification.recipient_user_id == user_id,
                )
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
        user_id: UUID | None,
        include_archived: bool,
        read: bool | None,
        severity: str | None,
        notification_type: str | None,
        source_type: str | None,
    ) -> Select[tuple[WorkspaceNotification]]:
        statement = select(WorkspaceNotification).where(
            WorkspaceNotification.workspace_id == workspace_id,
        )
        statement = statement.where(*self._preference_filters(workspace_id, user_id))
        if user_id is not None:
            statement = statement.where(
                or_(
                    WorkspaceNotification.recipient_user_id.is_(None),
                    WorkspaceNotification.recipient_user_id == user_id,
                )
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
        *,
        user_id: UUID | None,
    ) -> WorkspaceNotification:
        notification = self._session.scalar(
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.id == notification_id,
            )
        )
        if (
            notification is not None
            and user_id is not None
            and notification.recipient_user_id is not None
            and notification.recipient_user_id != user_id
        ):
            notification = None
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
        user_id: UUID | None,
        read: bool | None = None,
        include_archived: bool = True,
    ) -> int:
        statement = select(func.count()).where(WorkspaceNotification.workspace_id == workspace_id)
        statement = statement.where(*self._preference_filters(workspace_id, user_id))
        if user_id is not None:
            statement = statement.where(
                or_(
                    WorkspaceNotification.recipient_user_id.is_(None),
                    WorkspaceNotification.recipient_user_id == user_id,
                )
            )
        if not include_archived:
            statement = statement.where(WorkspaceNotification.archived_at.is_(None))
        if read is True:
            statement = statement.where(WorkspaceNotification.read_at.is_not(None))
        if read is False:
            statement = statement.where(WorkspaceNotification.read_at.is_(None))
        total = self._session.scalar(statement)
        return int(total or 0)

    def _count_archived(self, workspace_id: UUID, *, user_id: UUID | None) -> int:
        statement = select(func.count()).where(
            WorkspaceNotification.workspace_id == workspace_id,
            WorkspaceNotification.archived_at.is_not(None),
        )
        statement = statement.where(*self._preference_filters(workspace_id, user_id))
        if user_id is not None:
            statement = statement.where(
                or_(
                    WorkspaceNotification.recipient_user_id.is_(None),
                    WorkspaceNotification.recipient_user_id == user_id,
                )
            )
        total = self._session.scalar(statement)
        return int(total or 0)

    def _group_counts(
        self,
        workspace_id: UUID,
        column: InstrumentedAttribute[str],
        *,
        user_id: UUID | None,
    ) -> dict[str, int]:
        statement = select(column, func.count()).where(
            WorkspaceNotification.workspace_id == workspace_id
        )
        statement = statement.where(*self._preference_filters(workspace_id, user_id))
        if user_id is not None:
            statement = statement.where(
                or_(
                    WorkspaceNotification.recipient_user_id.is_(None),
                    WorkspaceNotification.recipient_user_id == user_id,
                )
            )
        rows = self._session.execute(statement.group_by(column))
        return {str(key): int(count) for key, count in rows}

    def _preference_filters(
        self,
        workspace_id: UUID,
        user_id: UUID | None,
    ) -> list[ColumnElement[bool]]:
        if user_id is None:
            return []
        preference = self.get_preferences(workspace_id, user_id)
        if preference is None:
            return []
        if not preference.in_app_enabled:
            return [false()]
        filters: list[ColumnElement[bool]] = []
        if not preference.announcement_enabled:
            filters.append(not_(WorkspaceNotification.notification_type.like("announcement%")))
        if not preference.task_enabled:
            filters.append(not_(WorkspaceNotification.notification_type.like("task%")))
        if not preference.approval_enabled:
            filters.append(not_(WorkspaceNotification.notification_type.like("approval%")))
        if not preference.security_enabled:
            filters.append(not_(WorkspaceNotification.notification_type.like("security%")))
        return filters

    def get_preferences(
        self,
        workspace_id: UUID,
        user_id: UUID,
    ) -> UserNotificationPreference | None:
        return self._session.scalar(
            select(UserNotificationPreference).where(
                UserNotificationPreference.workspace_id == workspace_id,
                UserNotificationPreference.user_id == user_id,
            )
        )

    def upsert_preferences(
        self,
        workspace_id: UUID,
        user_id: UUID,
        values: dict[str, bool | None],
    ) -> UserNotificationPreference:
        preference = self.get_preferences(workspace_id, user_id)
        if preference is None:
            preference = UserNotificationPreference(
                workspace_id=workspace_id,
                user_id=user_id,
            )
            self._session.add(preference)
            self._session.flush()
        for field, value in values.items():
            if value is not None:
                setattr(preference, field, value)
        self._session.commit()
        self._session.refresh(preference)
        return preference


class GovernanceNotificationService:
    def __init__(self, session: Session) -> None:
        self._notifications = NotificationCenterService(session)

    def record_audit_integrity(self, check: AuditIntegrityCheck) -> None:
        if check.valid:
            return
        self._notifications.create_once(
            check.workspace_id,
            NotificationCreateRequest(
                notification_type="audit.integrity_invalid",
                severity="critical",
                source_type="audit_integrity_check",
                source_id=check.workspace_id,
                title="Audit hash chain verification failed",
                body="The workspace audit chain needs immediate operator review.",
                metadata={
                    "check_id": str(check.id),
                    "broken_event_id": str(check.broken_event_id)
                    if check.broken_event_id is not None
                    else None,
                    "reason": check.reason,
                    "recommended_actions": [
                        "Stop audit-retention cleanup for this workspace.",
                        "Export the integrity check and affected audit range.",
                        "Investigate database and operator changes before remediation.",
                    ],
                    "drilldown": (
                        f"/api/v1/workspaces/{check.workspace_id}/operations/audit-integrity"
                    ),
                },
            ),
        )

    def record_model_attempt(self, record: ModelUsageRecord) -> None:
        if record.metering_status in {"unpriced", "missing_usage"}:
            self._notifications.create_once(
                record.workspace_id,
                NotificationCreateRequest(
                    notification_type=f"cost.{record.metering_status}",
                    severity="warning",
                    source_type="agent_run",
                    source_id=record.agent_run_id,
                    title=(
                        "Model usage has no pricing rule"
                        if record.metering_status == "unpriced"
                        else "Model provider did not report usage"
                    ),
                    body="Cost evidence is incomplete for a model attempt.",
                    metadata={
                        "model_usage_record_id": str(record.id),
                        "agent_run_id": str(record.agent_run_id),
                        "provider": record.provider,
                        "model": record.model,
                        "attempt_outcome": record.attempt_outcome,
                        "trace_id": record.trace_id,
                        "request_id": record.request_id,
                        "recommended_actions": [
                            "Configure an effective pricing rule."
                            if record.metering_status == "unpriced"
                            else "Verify provider SDK usage extraction and credentials."
                        ],
                        "drilldown": (
                            f"/api/v1/workspaces/{record.workspace_id}/costs/usage"
                            f"?trace_id={record.trace_id}"
                            if record.trace_id is not None
                            else f"/api/v1/workspaces/{record.workspace_id}/costs/usage"
                        ),
                    },
                ),
            )
        budget_state = record.budget_decision.get("budget_state")
        if budget_state not in {"warning", "exhausted"}:
            return
        budget_id = _uuid_or_none(record.budget_decision.get("budget_id"))
        self._notifications.create_once(
            record.workspace_id,
            NotificationCreateRequest(
                notification_type=f"cost.budget_{budget_state}",
                severity="critical" if budget_state == "exhausted" else "warning",
                source_type="workspace_cost_budget",
                source_id=budget_id,
                title=(
                    "Workspace model cost budget is exhausted"
                    if budget_state == "exhausted"
                    else "Workspace model cost budget is near its limit"
                ),
                body="Review model usage and budget enforcement before further execution.",
                metadata={
                    "budget_decision": record.budget_decision,
                    "recommended_actions": [
                        "Inspect the cost summary and high-cost runs.",
                        "Adjust the budget or reduce model usage after review.",
                    ],
                    "drilldown": f"/api/v1/workspaces/{record.workspace_id}/costs/summary",
                },
            ),
        )

    def record_budget_blocked(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        decision: dict[str, object],
    ) -> None:
        reason = decision.get("reason")
        self._notifications.create_once(
            workspace_id,
            NotificationCreateRequest(
                notification_type=(
                    "cost.pricing_missing"
                    if reason == "pricing_rule_missing"
                    else "cost.budget_exhausted"
                ),
                severity="critical",
                source_type="agent_run",
                source_id=run_id,
                title=(
                    "Model request blocked because pricing is missing"
                    if reason == "pricing_rule_missing"
                    else "Model request blocked by workspace budget"
                ),
                body="The model request was rejected before provider execution.",
                metadata={
                    "agent_run_id": str(run_id),
                    "budget_decision": decision,
                    "recommended_actions": [
                        "Configure an active pricing rule."
                        if reason == "pricing_rule_missing"
                        else "Review cost usage and update the blocking budget."
                    ],
                    "drilldown": f"/api/v1/workspaces/{workspace_id}/costs/summary",
                },
            ),
        )


def _uuid_or_none(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
