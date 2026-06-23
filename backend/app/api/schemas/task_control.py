from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload

CORRECTION_TARGET_PATTERN = "^(task|step|agent|artifact|final_output)$"
CORRECTION_MODE_PATTERN = "^(revise|regenerate|add_missing_work|replace_artifact|stop_work)$"


class TaskCorrectionRequest(BaseModel):
    target_type: str = Field(pattern=CORRECTION_TARGET_PATTERN)
    mode: str = Field(pattern=CORRECTION_MODE_PATTERN)
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
    correction_mode: str | None = Field(default=None, pattern=CORRECTION_MODE_PATTERN)
    target_type: str | None = Field(default=None, pattern=CORRECTION_TARGET_PATTERN)
    target_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskDeliveryDecisionRequest(BaseModel):
    action: str = Field(pattern="^(approve|request_changes|reject)$")
    summary: str = Field(min_length=1, max_length=4_000)
    instruction: str | None = Field(default=None, max_length=4_000)
    finalize: bool = True
    correction_mode: str | None = Field(default=None, pattern=CORRECTION_MODE_PATTERN)
    target_type: str | None = Field(default=None, pattern=CORRECTION_TARGET_PATTERN)
    target_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


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


class TaskDeliveryDecisionResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    action: str
    decision: str
    status: str
    task_status: str
    message_id: UUID
    created_step_id: UUID | None = None
    final_output: dict[str, object] | None = None
    details: dict[str, object]

    @field_serializer("final_output", "details")
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


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
