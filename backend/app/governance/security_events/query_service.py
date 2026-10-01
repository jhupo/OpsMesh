from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from backend.app.governance.security_events.models import SecurityEvent
from backend.app.shared.db.read_service import AdminSessionService
from backend.app.shared.pagination import PageParams


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
