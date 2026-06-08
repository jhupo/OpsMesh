from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.api.schemas.tasks import TaskHandoffQueueResponse, TaskManagerQueueResponse


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
    active_run_phase_counts: dict[str, int] = Field(default_factory=dict)
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
        return [
            redact_sensitive_payload(item.model_dump(mode="python"))
            for item in value
        ]


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
    suggested_actions: list[AgentTeamOperationsConsoleActionPlanItem] = Field(
        default_factory=list
    )

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
        return [
            redact_sensitive_payload(item.model_dump(mode="python"))
            for item in value
        ]


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
    suggested_actions: list[AgentTeamOperationsConsoleActionPlanItem] = Field(
        default_factory=list
    )

    @field_serializer("suggested_actions")
    def _serialize_suggested_actions(
        self,
        value: list[AgentTeamOperationsConsoleActionPlanItem],
    ) -> list[dict[str, object]]:
        return [
            redact_sensitive_payload(item.model_dump(mode="python"))
            for item in value
        ]


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


class AgentTeamCommandCenterApplyRequest(BaseModel):
    dry_run: bool = True
    sources: list[str] = Field(default_factory=list, max_length=5)
    actions: list[str] = Field(default_factory=list, max_length=10)
    max_actions: int = Field(default=5, ge=1, le=10)
    max_tasks_per_action: int = Field(default=100, ge=1, le=200)
    enqueue_runs: bool = False
    include_completed: bool = False
    queue_limit: int = Field(default=50, ge=1, le=200)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamCommandCenterApplyResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    dry_run: bool
    status: str
    requested_action_count: int
    eligible_action_count: int
    applied_action_count: int
    skipped_action_count: int
    scheduled_run_skip_reason: str | None = None
    scheduled_run_blocked_reasons: dict[str, int] = Field(default_factory=dict)
    scheduled_run_blocked_steps: list[dict[str, object]] = Field(default_factory=list)
    scheduled_run_count: int
    summary: dict[str, object]
    results: list[dict[str, object]]
    skipped: list[dict[str, object]]
    scheduled_runs: list[dict[str, object]]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("results", "skipped", "scheduled_run_blocked_steps", "scheduled_runs")
    def _serialize_items(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class AgentTeamExecutionLoopFinalizeRequest(BaseModel):
    dry_run: bool = True
    max_tasks: int = Field(default=50, ge=1, le=200)


class AgentTeamExecutionLoopRunRequest(BaseModel):
    dry_run: bool = True
    apply_command_center_actions: bool = True
    enqueue_runs: bool = True
    finalize_ready_tasks: bool = True
    sources: list[str] = Field(default_factory=list, max_length=5)
    actions: list[str] = Field(default_factory=list, max_length=10)
    max_actions: int = Field(default=5, ge=1, le=10)
    max_tasks_per_action: int = Field(default=100, ge=1, le=200)
    max_finalize_tasks: int = Field(default=50, ge=1, le=200)
    include_completed: bool = False
    queue_limit: int = Field(default=50, ge=1, le=200)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamExecutionLoopEnqueueRequest(BaseModel):
    idempotency_suffix: str = Field(default="api", min_length=1, max_length=160)
    priority: int = Field(default=0, ge=-100, le=100)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamExecutionLoopEnqueueResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    status: str
    queued: bool
    job_type: str
    queue_name: str


class AgentTeamRuntimeControlRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1_000)
    instruction: str | None = Field(default=None, max_length=4_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamRuntimeBindRequest(BaseModel):
    workspace_runtime_id: UUID
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamRuntimeEnsureLimitsRequest(BaseModel):
    cpu_count: float = Field(gt=0, le=8)
    memory_mb: int = Field(ge=128, le=32_768)
    disk_mb: int = Field(ge=256, le=102_400)
    timeout_seconds: int = Field(ge=1, le=3_600)
    max_output_bytes: int = Field(default=256_000, ge=1, le=2_000_000)
    max_processes: int = Field(default=256, ge=1, le=512)


class AgentTeamRuntimeEnsureRequest(BaseModel):
    template_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    limits: AgentTeamRuntimeEnsureLimitsRequest | None = None
    network_disabled: bool = True
    start: bool = True
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamRuntimeResponse(ORMModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    status: str
    team_session_id: UUID | None
    team_session_key: str | None
    thread_id: UUID | None
    workspace_runtime_id: UUID | None
    runtime_status: str | None
    runtime_space_id: UUID | None
    last_iteration: dict[str, object] | None
    last_message_at: datetime | None
    runtime_health: str
    member_session_count: int
    member_agent_ids: list[UUID]
    operating_policy: dict[str, object]
    memory_summary: dict[str, object]
    metadata: dict[str, object]

    @field_serializer("operating_policy", "memory_summary", "metadata", "last_iteration")
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamExecutionLoopFinalizeResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    dry_run: bool
    status: str
    scanned_task_count: int
    finalized_task_count: int
    skipped_task_count: int
    results: list[dict[str, object]]

    @field_serializer("results")
    def _serialize_results(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class AgentTeamExecutionLoopRunResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    dry_run: bool
    status: str
    summary: dict[str, object]
    command_center_actions: dict[str, object] | None
    finalization: dict[str, object] | None

    @field_serializer("summary", "command_center_actions", "finalization")
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamExecutionLoopStatusResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    status: str
    summary: dict[str, object]
    command_center: dict[str, object]
    finalization: dict[str, object]

    @field_serializer("summary", "command_center", "finalization")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentTeamOperatorActionRequest(BaseModel):
    action: str = Field(
        pattern=(
            "^(request_manager_review|reassign_step|requeue_blocked_steps|"
            "schedule_downstream_steps)$"
        )
    )
    task_ids: list[UUID] = Field(default_factory=list, max_length=100)
    task_step_ids: list[UUID] = Field(default_factory=list, max_length=200)
    agent_profile_id: UUID | None = None
    max_tasks: int = Field(default=25, ge=1, le=100)
    instruction: str | None = Field(default=None, max_length=4_000)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamOperatorActionResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    action: str
    agent_profile_id: UUID | None = None
    status: str
    requested_task_count: int
    applied_count: int
    skipped_count: int
    warnings: list[str] = Field(default_factory=list)
    results: list[dict[str, object]]

    @field_serializer("results")
    def _serialize_results(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]
