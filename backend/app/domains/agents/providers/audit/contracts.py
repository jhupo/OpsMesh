from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ModelProviderUsageAuditResponse(BaseModel):
    id: UUID
    action: str
    run_id: str | None
    task_id: str | None
    task_step_id: str | None
    agent_profile_id: str | None
    provider: str | None
    model: str | None
    model_api: str | None
    credential_id: str | None
    fallback_selected: bool | None
    reason: dict[str, object] | None = None
    failed_provider: dict[str, object] | None = None
    created_at: datetime
