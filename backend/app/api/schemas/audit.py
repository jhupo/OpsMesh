from datetime import datetime
from uuid import UUID

from backend.app.api.schemas.common import ORMModel


class AuditEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    actor_type: str
    actor_id: str
    user_id: UUID | None
    agent_run_id: UUID | None
    action: str
    target_type: str
    target_id: str
    audit_metadata: dict[str, object]
    created_at: datetime

