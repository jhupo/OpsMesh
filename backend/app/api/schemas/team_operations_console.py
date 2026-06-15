from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.api.schemas.tasks import TaskHandoffQueueResponse, TaskManagerQueueResponse
from backend.app.api.schemas.team_execution import AgentTeamExecutionOverviewResponse


class AgentTeamOperationsConsoleTeamResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    team_type: str
    description: str
    status: str
    manager_agent_profile_id: UUID | None
    runtime_space_id: UUID | None
    coordination_rules: dict[str, object]
    default_task_policy: dict[str, object]

    @field_serializer("coordination_rules", "default_task_policy")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentTeamOperationsConsoleRuntimeResponse(BaseModel):
    status: str
    workspace_runtime_id: UUID | None
    runtime_status: str | None
    runtime_space_id: UUID | None
    runtime_health: str
    thread_id: UUID | None
    team_session_id: UUID | None
    team_session_key: str | None
    member_session_count: int
    member_agent_ids: list[UUID]
    last_iteration: dict[str, object] | None
    last_message_at: datetime | None
    operating_policy: dict[str, object]
    memory_summary: dict[str, object]
    scheduling: dict[str, object] = Field(default_factory=dict)
    blocked_steps: dict[str, object] = Field(default_factory=dict)
    queue: dict[str, object] = Field(default_factory=dict)
    ready: bool
    metadata: dict[str, object]

    @field_serializer(
        "last_iteration",
        "operating_policy",
        "memory_summary",
        "scheduling",
        "blocked_steps",
        "queue",
        "metadata",
    )
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamOperationsConsoleCommandRuntimeResponse(BaseModel):
    status: str | None = None
    workspace_runtime_id: UUID | None = None
    runtime_status: str | None = None
    runtime_space_id: UUID | None = None
    thread_id: UUID | None = None
    team_session_id: UUID | None = None
    member_session_count: int = 0
    ready: bool = False
    operating_policy: dict[str, object] = Field(default_factory=dict)
    memory_summary: dict[str, object] = Field(default_factory=dict)

    @field_serializer("operating_policy", "memory_summary")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentTeamOperationsConsoleActionPlanItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str | None = None
    source_index: int | None = None
    automation: str | None = None
    action: str | None = None
    priority: int | None = None
    reason: str | None = None
    task_ids: list[UUID] = Field(default_factory=list)
    task_step_ids: list[UUID] = Field(default_factory=list)
    agent_profile_id: UUID | None = None


class AgentTeamOperationsConsoleQueuesResponse(BaseModel):
    handoff: TaskHandoffQueueResponse
    manager: TaskManagerQueueResponse


class AgentTeamOperationsConsoleCommandCenterResponse(BaseModel):
    workspace_id: UUID | None = None
    team_id: UUID | None = None
    generated_at: datetime | None = None
    summary: dict[str, object] = Field(default_factory=dict)
    runtime: AgentTeamOperationsConsoleCommandRuntimeResponse = Field(
        default_factory=AgentTeamOperationsConsoleCommandRuntimeResponse
    )
    provider_readiness: dict[str, object] = Field(default_factory=dict)
    operating_policy: dict[str, object] = Field(default_factory=dict)
    memory_summary: dict[str, object] = Field(default_factory=dict)
    overview: AgentTeamExecutionOverviewResponse | None = None
    queues: AgentTeamOperationsConsoleQueuesResponse | None = None
    action_plan: list[AgentTeamOperationsConsoleActionPlanItem] = Field(default_factory=list)

    @field_serializer("summary", "provider_readiness", "operating_policy", "memory_summary")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("action_plan")
    def _serialize_action_plan(
        self,
        value: list[AgentTeamOperationsConsoleActionPlanItem],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item.model_dump(mode="python")) for item in value]


class AgentTeamOperationsConsoleAgentSummary(BaseModel):
    id: UUID
    name: str
    role: str
    status: str
    model: str
    model_provider_credential_id: UUID | None
    model_provider: dict[str, object]

    @field_serializer("model_provider")
    def _serialize_model_provider(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentTeamOperationsConsoleSessionResponse(BaseModel):
    id: UUID
    session_key: str
    scope_type: str
    scope_id: str
    status: str
    agent_profile_id: UUID | None
    agent_team_id: UUID | None
    item_count: int
    openai_conversation_id: str | None
    latest_item_metadata: dict[str, object] | None
    updated_at: datetime

    @field_serializer("latest_item_metadata")
    def _serialize_latest_item_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamOperationsConsoleMemberMailboxResponse(BaseModel):
    unread_count: int
    latest_message_at: datetime | None


class AgentTeamOperationsConsoleMemberResponse(BaseModel):
    team_member_id: UUID
    agent_profile_id: UUID
    agent: AgentTeamOperationsConsoleAgentSummary | None
    reports_to_member_id: UUID | None
    team_role: str
    department: str | None
    position_title: str | None
    responsibilities: list[str]
    accepts_tasks: bool
    max_concurrent_tasks: int
    status: str
    session: AgentTeamOperationsConsoleSessionResponse | None
    mailbox: AgentTeamOperationsConsoleMemberMailboxResponse


class AgentTeamOperationsConsoleProviderManagementResponse(BaseModel):
    credential_count: int = 0
    active_credential_count: int = 0
    default_credential_id: UUID | None = None
    credentials: list[dict[str, object]] = Field(default_factory=list)
    agent_bindings: list[dict[str, object]] = Field(default_factory=list)
    run_diagnostics: dict[str, object] = Field(default_factory=dict)
    suggested_actions: list[AgentTeamOperationsConsoleActionPlanItem] = Field(default_factory=list)

    @field_serializer("credentials", "agent_bindings")
    def _serialize_provider_management_items(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]

    @field_serializer("run_diagnostics")
    def _serialize_run_diagnostics(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("suggested_actions")
    def _serialize_suggested_actions(
        self,
        value: list[AgentTeamOperationsConsoleActionPlanItem],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item.model_dump(mode="python")) for item in value]


class AgentTeamMemberModelProviderUpdateRequest(BaseModel):
    model_provider_credential_id: UUID | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    model_api: str | None = Field(default=None, min_length=1, max_length=80)
    reset_session: bool = True


class AgentTeamOperationsConsoleSessionsResponse(BaseModel):
    team_session: AgentTeamOperationsConsoleSessionResponse | None
    member_sessions: list[AgentTeamOperationsConsoleSessionResponse]
    total: int


class AgentTeamOperationsConsoleMessageResponse(BaseModel):
    id: UUID
    thread_id: UUID
    task_id: UUID | None
    agent_team_id: UUID | None
    sender_agent_profile_id: UUID | None
    recipient_agent_profile_id: UUID | None
    message_type: str
    status: str
    read_at: datetime | None
    created_at: datetime
    body_preview: str
    payload: dict[str, object]

    @field_serializer("payload")
    def _serialize_payload(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentTeamOperationsConsoleMailboxResponse(BaseModel):
    thread_id: UUID | None
    unread_count: int
    latest_messages: list[AgentTeamOperationsConsoleMessageResponse]


class AgentTeamOperationsConsoleControlsResponse(BaseModel):
    can_start: bool
    can_pause: bool
    can_resume: bool
    can_stop: bool
    can_ensure_workspace_runtime: bool
    suggested_actions: list[AgentTeamOperationsConsoleActionPlanItem] = Field(default_factory=list)

    @field_serializer("suggested_actions")
    def _serialize_suggested_actions(
        self,
        value: list[AgentTeamOperationsConsoleActionPlanItem],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item.model_dump(mode="python")) for item in value]


class AgentTeamOperationsConsoleReadinessResponse(BaseModel):
    status: str
    ready: bool
    runtime_health: str
    runtime_status: str
    workspace_runtime_status: str | None = None
    stall: dict[str, object] = Field(default_factory=dict)
    mailbox_unread_count: int = 0
    queue_backlog: int = 0
    blocked_step_count: int = 0
    provider_blocked: bool = False
    action_plan_count: int = 0
    next_operator_action: dict[str, object] | None = None

    @field_serializer("stall", "next_operator_action")
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamOperationsConsoleResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    team: AgentTeamOperationsConsoleTeamResponse
    runtime: AgentTeamOperationsConsoleRuntimeResponse
    command_center: AgentTeamOperationsConsoleCommandCenterResponse
    members: list[AgentTeamOperationsConsoleMemberResponse]
    provider_management: AgentTeamOperationsConsoleProviderManagementResponse
    sessions: AgentTeamOperationsConsoleSessionsResponse
    mailbox: AgentTeamOperationsConsoleMailboxResponse
    controls: AgentTeamOperationsConsoleControlsResponse
    readiness: AgentTeamOperationsConsoleReadinessResponse
