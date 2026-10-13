from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from opsmesh.shared.contracts import FromAttributesContract
from opsmesh.shared.security.redaction import redact_sensitive_payload


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


class AgentTeamOrchestrationFinalizeRequest(BaseModel):
    dry_run: bool = True
    max_tasks: int = Field(default=50, ge=1, le=200)


class AgentTeamOrchestrationAdvanceRequest(BaseModel):
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


class AgentTeamOrchestrationSubmitRequest(AgentTeamOrchestrationAdvanceRequest):
    dry_run: Literal[False] = False


class AgentTeamOrchestrationReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed"]
    reason: str = Field(min_length=1, max_length=1_000)
    evidence: dict[str, object] = Field(min_length=1)
    observed_result: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_confirmation(self) -> AgentTeamOrchestrationReconcileRequest:
        if not self.reason.strip():
            raise ValueError("Reconciliation requires a reason")
        if len(self.model_dump_json().encode("utf-8")) > 65_536:
            raise ValueError("Reconciliation evidence exceeds 64 KiB")
        return self


class TeamOrchestrationResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    request_id: UUID
    workflow_id: str
    status: str
    result: dict[str, object] | None = None
    last_error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_serializer("result")
    def _serialize_result(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamRuntimeControlRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1_000)
    instruction: str | None = Field(default=None, max_length=4_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamRuntimeBindRequest(BaseModel):
    workspace_runtime_id: UUID
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamRuntimeEnsureRequest(BaseModel):
    template_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    start: bool = True
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentTeamRuntimeResponse(FromAttributesContract):
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
    last_advancement: dict[str, object] | None
    last_message_at: datetime | None
    runtime_health: str
    member_session_count: int
    member_agent_ids: list[UUID]
    operating_policy: dict[str, object]
    memory_summary: dict[str, object]
    metadata: dict[str, object]

    @field_serializer("operating_policy", "memory_summary", "metadata", "last_advancement")
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentTeamOrchestrationFinalizeResponse(BaseModel):
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


class AgentTeamOrchestrationAdvanceResponse(BaseModel):
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
