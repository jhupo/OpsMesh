from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from backend.app.api.schemas.capabilities.catalog import CapabilityTeamPolicy
from backend.app.api.schemas.common import ORMModel, TimestampedModel


class AgentTeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    team_type: str = Field(default="general", max_length=80)
    description: str = Field(default="", max_length=2_000)
    manager_agent_profile_id: UUID | None = None
    runtime_space_id: UUID | None = None
    coordination_rules: dict[str, object] = Field(default_factory=dict)
    default_task_policy: dict[str, object] = Field(default_factory=dict)
    capability_policy: CapabilityTeamPolicy = Field(default_factory=CapabilityTeamPolicy)


class AgentTeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    team_type: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2_000)
    manager_agent_profile_id: UUID | None = None
    runtime_space_id: UUID | None = None
    coordination_rules: dict[str, object] | None = None
    default_task_policy: dict[str, object] | None = None
    status: str | None = Field(default=None, pattern="^(active|archived)$")

    @model_validator(mode="after")
    def _require_change(self) -> AgentTeamUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one team field is required")
        return self

    model_config = {"from_attributes": True}


class AgentTeamResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    team_type: str
    description: str
    manager_agent_profile_id: UUID | None
    runtime_space_id: UUID | None
    coordination_rules: dict[str, object]
    default_task_policy: dict[str, object]
    capability_policy: CapabilityTeamPolicy
    capability_policy_version: int
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


class AgentTeamMemberUpdateRequest(BaseModel):
    reports_to_member_id: UUID | None = None
    team_role: str | None = Field(default=None, min_length=1, max_length=80)
    department: str | None = Field(default=None, max_length=120)
    position_title: str | None = Field(default=None, max_length=160)
    responsibilities: list[str] | None = None
    skill_weights: dict[str, object] | None = None
    availability: dict[str, object] | None = None
    max_concurrent_tasks: int | None = Field(default=None, ge=1, le=100)
    accepts_tasks: bool | None = None
    is_required: bool | None = None
    order_index: int | None = None
    status: str | None = Field(default=None, pattern="^(active|inactive)$")


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


class AgentTeamOrgAgentSummary(BaseModel):
    id: UUID
    name: str
    role: str
    status: str


class AgentTeamOrgMemberNode(BaseModel):
    id: UUID
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
    agent: AgentTeamOrgAgentSummary | None
    children: list[AgentTeamOrgMemberNode] = Field(default_factory=list)


class AgentTeamOrgChartResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    name: str
    team_type: str
    description: str
    status: str
    manager_agent_profile_id: UUID | None
    manager_agent: AgentTeamOrgAgentSummary | None
    runtime_space_id: UUID | None
    coordination_rules: dict[str, object]
    default_task_policy: dict[str, object]
    capability_policy: CapabilityTeamPolicy
    capability_policy_version: int
    roots: list[AgentTeamOrgMemberNode]
    members: list[AgentTeamOrgMemberNode]
    orphan_member_ids: list[UUID]
    cycle_member_ids: list[UUID]
    capacity_summary: dict[str, int]
