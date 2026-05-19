from datetime import datetime
from uuid import UUID

from backend.app.api.schemas.common import ORMModel, TimestampedModel


class AgentRunResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    runtime_id: UUID | None
    runtime_space_id: UUID | None
    status: str
    input: dict[str, object]
    output: dict[str, object] | None
    error: dict[str, object] | None
    model: str | None
    started_at: datetime | None
    completed_at: datetime | None


class RunEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    agent_run_id: UUID
    event_type: str
    sequence: int
    message: str
    event_metadata: dict[str, object]
    created_at: datetime
