from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import ORMModel, TimestampedModel


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


class AgentTeamMemberCreateRequest(BaseModel):
    agent_profile_id: UUID
    reports_to_member_id: UUID | None = None
    team_role: str = Field(min_length=1, max_length=80)
    department: str | None = Field(default=None, max_length=120)
    position_title: str | None = Field(default=None, max_length=160)
    responsibilities: list[str] = Field(default_factory=list)
    skill_weights: dict[str, object] = Field(default_factory=dict)
    availability: dict[str, object] = Field(default_factory=dict)
    max_concurrent_tasks: int = Field(default=1, ge=1, le=100)
    accepts_tasks: bool = True
    is_required: bool = True
    order_index: int = 0


class AgentTeamMemberResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    agent_team_id: UUID
    agent_profile_id: UUID
    reports_to_member_id: UUID | None
    team_role: str
    department: str | None
    position_title: str | None
    responsibilities: list[str]
    skill_weights: dict[str, object]
    availability: dict[str, object]
    max_concurrent_tasks: int
    accepts_tasks: bool
    is_required: bool
    order_index: int
    status: str
