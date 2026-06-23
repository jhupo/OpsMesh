from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.redaction import (
    redact_sensitive_payload,
    redact_sensitive_payload_item,
)


class AgentTeamExecutionMemberResponse(BaseModel):
    team_member_id: UUID
    agent_profile_id: UUID
    reports_to_member_id: UUID | None = None
    agent_name: str | None
    agent_role: str | None
    team_role: str
    department: str | None
    position_title: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    status: str
    accepts_tasks: bool
    max_concurrent_tasks: int
    active_task_count: int
    workspace_active_task_count: int = 0
    workspace_active_task_ids: list[UUID] = Field(default_factory=list)
    active_step_count: int
    active_run_count: int
    active_run_phase_counts: dict[str, int] = Field(default_factory=dict)
    utilization: float
    overloaded: bool
    at_capacity: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)


class AgentTeamExecutionTaskResponse(BaseModel):
    task_id: UUID
    title: str
    status: str
    priority: int
    domain_type: str | None
    summary_status: str
    pending_phase: str
    risk_level: str
    attention_score: int
    needs_attention: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    step_status_counts: dict[str, int]
    active_run_count: int
    active_run_phase_counts: dict[str, int] = Field(default_factory=dict)
    last_activity_at: datetime


class AgentTeamExecutionStaffingGapResponse(BaseModel):
    required_role: str | None
    required_skills: list[str]
    step_count: int
    task_count: int
    task_ids: list[UUID]
    task_step_ids: list[UUID]
    matching_member_count: int
    recommended_action: str


class AgentTeamExecutionOverviewResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    team: dict[str, object]
    manager_agent: dict[str, object] | None
    summary: dict[str, object]
    members: list[AgentTeamExecutionMemberResponse]
    tasks: list[AgentTeamExecutionTaskResponse]
    staffing_gaps: list[AgentTeamExecutionStaffingGapResponse] = Field(default_factory=list)

    @field_serializer("team", "manager_agent", "summary")
    def _serialize_metadata(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamProjectDashboardResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    team: dict[str, object]
    summary: dict[str, object]
    tasks: list[dict[str, object]]

    @field_serializer("team", "summary")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("tasks")
    def _serialize_tasks(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class AgentTeamProjectSpaceResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    team: dict[str, object]
    summary: dict[str, object]
    runtime_spaces: list[dict[str, object]] = Field(default_factory=list)
    capacity: dict[str, object]
    storage: dict[str, object]
    memory: dict[str, object]
    employee_outputs: list[dict[str, object]] = Field(default_factory=list)
    project_tasks: list[dict[str, object]] = Field(default_factory=list)
    landing_rules: dict[str, object]
    space_tiers: list[dict[str, object]] = Field(default_factory=list)
    max_project_space: dict[str, object]
    archive_policy: dict[str, object]

    @field_serializer(
        "team",
        "summary",
        "runtime_spaces",
        "capacity",
        "storage",
        "memory",
        "employee_outputs",
        "project_tasks",
        "landing_rules",
        "space_tiers",
        "max_project_space",
        "archive_policy",
    )
    def _serialize_project_space(self, value: object) -> object:
        return redact_sensitive_payload_item(value)


class AgentTeamCommandCenterResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    runtime: dict[str, object]
    provider_readiness: dict[str, object]
    operating_policy: dict[str, object]
    memory_summary: dict[str, object]
    overview: dict[str, object]
    queues: dict[str, object]
    action_plan: list[dict[str, object]] = Field(default_factory=list)

    @field_serializer(
        "summary",
        "runtime",
        "provider_readiness",
        "operating_policy",
        "memory_summary",
        "overview",
        "queues",
    )
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("action_plan")
    def _serialize_action_plan(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class WorkspaceTeamCommandCenterResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    team_summaries: list[dict[str, object]] = Field(default_factory=list)
    project_task_items: list[dict[str, object]] = Field(default_factory=list)
    employee_load: list[dict[str, object]] = Field(default_factory=list)
    blocked_reasons: list[dict[str, object]] = Field(default_factory=list)
    cross_project_action_plan: list[dict[str, object]] = Field(default_factory=list)

    @field_serializer(
        "summary",
        "team_summaries",
        "project_task_items",
        "employee_load",
        "blocked_reasons",
        "cross_project_action_plan",
    )
    def _serialize_metadata(self, value: object) -> object:
        return redact_sensitive_payload_item(value)
