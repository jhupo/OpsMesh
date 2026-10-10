from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.service import AuditService
from opsmesh.shared.db.read_service import AdminSessionService
from opsmesh.shared.pagination import PageParams


class AdminSystemLogService(AdminSessionService):
    def list_audit_events(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        actor_type: str | None = None,
        target_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[AuditEvent], int]:
        statement = select(AuditEvent)
        if workspace_id is not None:
            statement = statement.where(AuditEvent.workspace_id == workspace_id)
        if user_id is not None:
            statement = statement.where(AuditEvent.user_id == user_id)
        if action is not None:
            statement = statement.where(AuditEvent.action == action)
        if actor_type is not None:
            statement = statement.where(AuditEvent.actor_type == actor_type)
        if target_type is not None:
            statement = statement.where(AuditEvent.target_type == target_type)
        if created_after is not None:
            statement = statement.where(AuditEvent.created_at >= created_after)
        if created_before is not None:
            statement = statement.where(AuditEvent.created_at <= created_before)
        statement = AuditService(self._session).apply_retention_to_statement(statement)
        ordered = statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        return self._page(ordered, page)
