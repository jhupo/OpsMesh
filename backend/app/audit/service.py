from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.audit.models import AuditEvent


class AuditService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_user_action(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID | str,
        metadata: dict[str, object] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            workspace_id=workspace_id,
            actor_type="user",
            actor_id=str(user_id),
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            audit_metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event
