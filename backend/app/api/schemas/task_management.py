from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload


class TaskPlanRetryRequest(BaseModel):
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = False
    enqueue: bool = True


class TaskPlanRegenerateRequest(BaseModel):
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = True
    enqueue: bool = False


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
                redact_sensitive_payload(item) if isinstance(item, dict) else item for item in value
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


class TaskCollaborationStateResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    task: dict[str, object]
    summary: dict[str, object]
    participants: list[dict[str, object]]
    phases: list[dict[str, object]]
    handoffs: list[dict[str, object]]
    manager: dict[str, object]

    @field_serializer("task", "summary", "manager")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("participants", "phases", "handoffs")
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class TaskCollaborationRecoveryPlanResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    status: str
    summary: dict[str, object]
    collaboration: dict[str, object]
    action_plan: list[dict[str, object]]
    skipped: list[dict[str, object]]

    @field_serializer("summary", "collaboration")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("action_plan", "skipped")
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class TaskCollaborationRecoveryApplyRequest(BaseModel):
    dry_run: bool = True
    actions: list[str] = Field(default_factory=list, max_length=10)
    sources: list[str] = Field(default_factory=list, max_length=10)
    max_actions: int = Field(default=10, ge=1, le=50)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskCollaborationRecoveryApplyResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    dry_run: bool
    status: str
    requested_action_count: int
    eligible_action_count: int
    applied_action_count: int
    failed_action_count: int
    skipped_action_count: int
    summary: dict[str, object]
    results: list[dict[str, object]]
    skipped: list[dict[str, object]]

    @field_serializer("summary")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("results", "skipped")
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


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
    org_health: dict[str, object] = Field(default_factory=dict)
    packages: list[TaskPlanPackageDiagnosticResponse]
    dependency_graph: dict[str, object]
    blocked_reasons: list[str]

    @field_serializer("summary", "manager", "org_health", "dependency_graph")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
