from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class TaskCreateRequest(BaseModel):
    agent_team_id: UUID | None = None
    runtime_space_id: UUID | None = None
    domain_type: str = Field(default="general", max_length=80)
    title: str = Field(min_length=1, max_length=240)
    description: str = ""
    priority: int = 0
    input: dict[str, object] = Field(default_factory=dict)
    generic_state: dict[str, object] = Field(default_factory=dict)
    domain_state: dict[str, object] = Field(default_factory=dict)


class TaskPlanRetryRequest(BaseModel):
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = False
    enqueue: bool = True


class TaskPlanRegenerateRequest(BaseModel):
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = True
    enqueue: bool = False


class TaskCorrectionRequest(BaseModel):
    target_type: str = Field(pattern="^(task|step|agent|artifact|final_output)$")
    mode: str = Field(pattern="^(revise|regenerate|add_missing_work|replace_artifact|stop_work)$")
    instruction: str = Field(min_length=1, max_length=4_000)
    target_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskOperatorActionRequest(BaseModel):
    action: str = Field(
        pattern=(
            "^(requeue_blocked_steps|reassign_step|request_manager_review|"
            "schedule_downstream_steps)$"
        )
    )
    task_step_ids: list[UUID] = Field(default_factory=list, max_length=100)
    agent_profile_id: UUID | None = None
    instruction: str | None = Field(default=None, max_length=4_000)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskControlActionRequest(BaseModel):
    action: str = Field(pattern="^(pause|resume|add_instruction|create_correction|cancel)$")
    instruction: str | None = Field(default=None, max_length=4_000)
    reason: str | None = Field(default=None, max_length=1_000)
    enqueue: bool = True
    correction_mode: str | None = Field(
        default=None,
        pattern="^(revise|regenerate|add_missing_work|replace_artifact|stop_work)$",
    )
    target_type: str | None = Field(
        default=None,
        pattern="^(task|step|agent|artifact|final_output)$",
    )
    target_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    created_by_agent_run_id: UUID | None
    agent_team_id: UUID | None
    runtime_space_id: UUID | None
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

    @field_serializer("input")
    def _serialize_input(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("generic_state")
    def _serialize_generic_state(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("domain_state")
    def _serialize_domain_state(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("team_snapshot")
    def _serialize_team_snapshot(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None

    @field_serializer("project_plan")
    def _serialize_project_plan(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None

    @field_serializer("final_output")
    def _serialize_final_output(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


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

    @field_serializer("payload")
    def _serialize_payload(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskLiveStatusResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    task: dict[str, object]
    summary: dict[str, object]
    steps: list[dict[str, object]]
    active_runs: list[dict[str, object]]
    recent_messages: list[dict[str, object]]

    @field_serializer("task", "summary")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("steps", "active_runs", "recent_messages")
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class TaskPlanningAttemptResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    planner_agent_profile_id: UUID | None
    attempt_number: int
    status: str
    strategy: str
    input_snapshot: dict[str, object]
    output_snapshot: dict[str, object] | None
    validation_errors: list[str]
    retry_count: int
    created_at: datetime
    completed_at: datetime | None


class TaskCorrectionResponse(BaseModel):
    task_id: UUID
    mode: str
    target_type: str
    created_step_id: UUID | None
    message_id: UUID
    status: str


class TaskOperatorActionResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    action: str
    status: str
    task_status: str
    changed_step_ids: list[UUID]
    created_step_ids: list[UUID]
    message_id: UUID
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, object]

    @field_serializer("details")
    def _serialize_details(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskControlActionResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    action: str
    status: str
    task_status: str
    message_id: UUID | None = None
    changed_step_ids: list[UUID] = Field(default_factory=list)
    scheduled_run_ids: list[UUID] = Field(default_factory=list)
    details: dict[str, object]

    @field_serializer("details")
    def _serialize_details(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskCorrectionStepDiagnostic(BaseModel):
    id: UUID
    work_package_id: str | None
    title: str
    status: str
    order_index: int
    expected_artifacts: list[str]
    result_summary: str | None


class TaskCorrectionRunDiagnostic(BaseModel):
    id: UUID
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error: dict[str, object] | None

    @field_serializer("error")
    def _serialize_error(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class TaskCorrectionArtifactDiagnostic(BaseModel):
    id: UUID
    filename: str
    artifact_type: str
    content_type: str | None
    version: int
    review_status: str
    work_package_id: str | None


class TaskCorrectionDiagnosticItem(BaseModel):
    message_id: UUID
    sequence: int
    mode: str
    target_type: str | None
    target: dict[str, object]
    instruction: str
    metadata: dict[str, object]
    actor_user_id: UUID | None
    created_step: TaskCorrectionStepDiagnostic | None
    runs: list[TaskCorrectionRunDiagnostic]
    artifacts: list[TaskCorrectionArtifactDiagnostic]
    status: str
    blocked_reasons: list[str]
    created_at: datetime

    @field_serializer("target", "metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskCorrectionDiagnosticsResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    corrections: list[TaskCorrectionDiagnosticItem]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskObservationCard(BaseModel):
    card_type: str
    title: str
    status: str
    data: dict[str, object]

    @field_serializer("data")
    def _serialize_data(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskObservationSection(BaseModel):
    key: str
    title: str
    cards: list[TaskObservationCard]


class TaskObservationResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    view_type: str
    generated_at: datetime
    summary: dict[str, object]
    sections: list[TaskObservationSection]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskTimelineAgent(BaseModel):
    id: UUID
    name: str
    role: str
    status: str


class TaskTimelineEvent(BaseModel):
    occurred_at: datetime
    source_type: str
    event_type: str
    phase: str
    status: str
    title: str
    summary: str
    task_step_id: UUID | None
    agent_run_id: UUID | None
    agent_profile_id: UUID | None
    artifact_id: UUID | None
    sequence: int
    agent: TaskTimelineAgent | None
    metadata: dict[str, object]

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskTimelineResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    events: list[TaskTimelineEvent]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskManagerDiagnosticsResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    manager: dict[str, object]
    summary: dict[str, object]
    handoff_chain: list[dict[str, object]]
    acceptance_decisions: list[dict[str, object]]
    follow_up_cycles: list[dict[str, object]]
    blocked_reasons: list[str]

    @field_serializer(
        "manager",
        "summary",
        "handoff_chain",
        "acceptance_decisions",
        "follow_up_cycles",
    )
    def _serialize_metadata(self, value: object) -> object:
        if isinstance(value, dict):
            return redact_sensitive_payload(value)
        if isinstance(value, list):
            return [
                redact_sensitive_payload(item) if isinstance(item, dict) else item
                for item in value
            ]
        return value


class TaskManagerQueueItemResponse(BaseModel):
    task_id: UUID
    team_id: UUID | None
    title: str
    status: str
    priority: int
    domain_type: str | None
    manager_agent_profile_id: UUID | None
    manager_agent_name: str | None
    manager_status: str
    summary_status: str
    pending_phase: str
    needs_attention: bool
    blocked_reasons: list[str]
    recommended_actions: list[str]
    acceptance_decisions: int
    follow_up_cycles: int
    step_status_counts: dict[str, int]
    last_activity_at: datetime


class TaskManagerQueueResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID | None = None
    generated_at: datetime
    total: int
    limit: int
    offset: int
    summary: dict[str, object]
    items: list[TaskManagerQueueItemResponse]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskExecutionAgentSummary(BaseModel):
    id: UUID
    name: str
    role: str
    status: str


class TaskExecutionRunDiagnostic(BaseModel):
    id: UUID
    status: str
    agent_profile_id: UUID | None
    runtime_id: UUID | None
    runtime_space_id: UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    error: dict[str, object] | None

    @field_serializer("error")
    def _serialize_error(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class TaskExecutionStepDiagnostic(BaseModel):
    task_step_id: UUID
    work_package_id: str | None
    title: str
    description: str
    status: str
    order_index: int
    required_role: str | None
    required_skills: list[str]
    expected_artifacts: list[str]
    acceptance_criteria: list[str]
    review_policy: dict[str, object]
    dependencies: dict[str, object]
    dependency_state: dict[str, object]
    assigned_agent: TaskExecutionAgentSummary | None
    assignment_status: str
    runnable: bool
    blocked_reasons: list[str]
    scheduling: dict[str, object]
    handoff: dict[str, object]
    runs: list[TaskExecutionRunDiagnostic]
    active_run_ids: list[UUID]
    result_summary: str | None

    @field_serializer(
        "review_policy",
        "dependencies",
        "dependency_state",
        "scheduling",
        "handoff",
    )
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskExecutionDiagnosticsResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    task: dict[str, object]
    summary: dict[str, object]
    steps: list[TaskExecutionStepDiagnostic]

    @field_serializer("task", "summary")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskHandoffQueueItemResponse(BaseModel):
    task_id: UUID
    task_title: str
    task_status: str
    task_priority: int
    team_id: UUID | None
    domain_type: str
    task_step_id: UUID
    work_package_id: str | None
    step_title: str
    step_status: str
    assigned_agent: dict[str, object] | None
    assignment_status: str
    handoff_status: str
    requires_handoff: bool
    upstream_step_ids: list[UUID]
    downstream_step_ids: list[UUID]
    runnable_downstream_step_ids: list[UUID]
    blocked_downstream_step_ids: list[UUID]
    blocked_reasons: list[str]
    recommended_actions: list[str]
    last_activity_at: datetime

    @field_serializer("assigned_agent")
    def _serialize_assigned_agent(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class TaskHandoffQueueResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID | None = None
    generated_at: datetime
    total: int
    limit: int
    offset: int
    summary: dict[str, object]
    items: list[TaskHandoffQueueItemResponse]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskPlanPackageMatchResponse(BaseModel):
    agent_profile_id: UUID
    team_member_id: UUID | None
    team_role: str
    score: float
    reasons: list[str]
    current_load: int
    max_concurrent_tasks: int


class TaskPlanPackageDiagnosticResponse(BaseModel):
    package_id: str | None
    title: str
    required_role: str
    required_skills: list[str]
    assigned_agent_profile_id: UUID | None
    assignment_status: str
    depends_on: list[str]
    expected_artifacts: list[str]
    acceptance_criteria: list[str]
    review_policy: dict[str, object]
    recommended_matches: list[TaskPlanPackageMatchResponse]

    @field_serializer("review_policy")
    def _serialize_review_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskPlanDiagnosticsResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    plan_present: bool
    plan_id: str | None
    strategy: str | None
    summary: dict[str, object]
    manager: dict[str, object]
    packages: list[TaskPlanPackageDiagnosticResponse]
    dependency_graph: dict[str, object]
    blocked_reasons: list[str]

    @field_serializer("summary", "manager", "dependency_graph")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
