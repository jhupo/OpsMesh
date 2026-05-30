from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class AgentTeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    team_type: str = Field(default="general", max_length=80)
    description: str = Field(default="", max_length=2_000)
    manager_agent_profile_id: UUID | None = None
    runtime_space_id: UUID | None = None
    coordination_rules: dict[str, object] = Field(default_factory=dict)
    default_task_policy: dict[str, object] = Field(default_factory=dict)


class AgentTeamResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    team_type: str
    description: str
    manager_agent_profile_id: UUID | None
    runtime_space_id: UUID | None
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
    roots: list[AgentTeamOrgMemberNode]
    members: list[AgentTeamOrgMemberNode]
    orphan_member_ids: list[UUID]
    cycle_member_ids: list[UUID]
    capacity_summary: dict[str, int]


class AgentTeamExecutionMemberResponse(BaseModel):
    team_member_id: UUID
    agent_profile_id: UUID
    agent_name: str | None
    agent_role: str | None
    team_role: str
    department: str | None
    status: str
    accepts_tasks: bool
    max_concurrent_tasks: int
    active_task_count: int
    active_step_count: int
    active_run_count: int
    utilization: float
    overloaded: bool
    blocked_reasons: list[str] = Field(default_factory=list)


class AgentTeamExecutionTaskResponse(BaseModel):
    task_id: UUID
    title: str
    status: str
    priority: int
    domain_type: str | None
    summary_status: str
    pending_phase: str
    needs_attention: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    step_status_counts: dict[str, int]
    active_run_count: int
    last_activity_at: datetime


class AgentTeamExecutionOverviewResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    team: dict[str, object]
    manager_agent: dict[str, object] | None
    summary: dict[str, object]
    members: list[AgentTeamExecutionMemberResponse]
    tasks: list[AgentTeamExecutionTaskResponse]

    @field_serializer("team", "manager_agent", "summary")
    def _serialize_metadata(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None
