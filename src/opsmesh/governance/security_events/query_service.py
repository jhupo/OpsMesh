from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.shared.db.read_service import AdminSessionService
from opsmesh.shared.pagination import PageParams


class AdminSecurityEventService(AdminSessionService):
    def list_security_events(
        self,
        page: PageParams,
        *,
        severity: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[SecurityEvent], int]:
        statement = select(SecurityEvent)
        if severity is not None:
            statement = statement.where(SecurityEvent.severity == severity)
        if workspace_id is not None:
            statement = statement.where(SecurityEvent.workspace_id == workspace_id)
        return self._page(statement.order_by(SecurityEvent.created_at.desc()), page)
