from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from backend.app.api.schemas.common import ORMModel


class ApprovalDecisionRequest(BaseModel):
    reason: str | None = None


class ApprovalResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    task_id: UUID | None
    agent_run_id: UUID | None
    requested_by_agent_profile_id: UUID | None
    approval_type: str
    risk_level: str
    payload: dict[str, object]
    status: str
    decided_by_user_id: UUID | None
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None

