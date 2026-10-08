from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.governance.audit.models import AuditEvent
from backend.app.governance.audit.service import AuditService
from backend.app.shared.config import Settings
from backend.app.shared.db.pagination import page_scalars
from backend.app.shared.pagination import PageParams


class AuditQueryService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def list_events(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[AuditEvent], int]:
        statement = (
            AuditService(self._session, self._settings)
            .apply_retention_to_statement(
                select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
            )
            .order_by(AuditEvent.created_at.desc())
        )
        return page_scalars(self._session, statement, page)
