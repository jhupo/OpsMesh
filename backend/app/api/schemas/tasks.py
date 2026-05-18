from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import TimestampedModel


class TaskCreateRequest(BaseModel):
    agent_team_id: UUID | None = None
    domain_type: str = Field(default="general", max_length=80)
    title: str = Field(min_length=1, max_length=240)
    description: str = ""
    priority: int = 0
    input: dict[str, object] = Field(default_factory=dict)
    generic_state: dict[str, object] = Field(default_factory=dict)
    domain_state: dict[str, object] = Field(default_factory=dict)


class TaskResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    created_by_agent_run_id: UUID | None
    agent_team_id: UUID | None
    domain_type: str
    title: str
    description: str
    status: str
    priority: int
    input: dict[str, object]
    generic_state: dict[str, object]
    domain_state: dict[str, object]
    team_snapshot: dict[str, object] | None
    project_plan: dict[str, object] | None
    final_output: dict[str, object] | None
    completed_at: datetime | None


class TaskMessageResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID
    task_step_id: UUID | None
    agent_run_id: UUID | None
    agent_profile_id: UUID | None
    message_type: str
    sequence: int
    body: str
    payload: dict[str, object]
