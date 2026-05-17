from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import TimestampedModel


class AgentTeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    team_type: str = Field(default="general", max_length=80)
    description: str = Field(default="", max_length=2_000)
    manager_agent_profile_id: UUID | None = None
    coordination_rules: dict[str, object] = Field(default_factory=dict)
    default_task_policy: dict[str, object] = Field(default_factory=dict)


class AgentTeamResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    team_type: str
    description: str
    manager_agent_profile_id: UUID | None
    coordination_rules: dict[str, object]
    default_task_policy: dict[str, object]
    status: str

