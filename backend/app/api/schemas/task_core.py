from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class TaskCreateRequest(BaseModel):
    agent_team_id: UUID | None = None
    runtime_space_id: UUID | None = None
    workspace_project_id: UUID | None = None
    domain_type: str = Field(default="general", max_length=80)
    title: str = Field(min_length=1, max_length=240)
    description: str = ""
    priority: int = 0
    input: dict[str, object] = Field(default_factory=dict)
    generic_state: dict[str, object] = Field(default_factory=dict)
    domain_state: dict[str, object] = Field(default_factory=dict)


class TaskFeedbackRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4_000)
    feedback_kind: str = Field(
        default="comment",
        pattern="^(comment|praise|concern|correction)$",
    )
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    created_by_agent_run_id: UUID | None
    agent_team_id: UUID | None
    runtime_space_id: UUID | None
    workspace_project_id: UUID | None
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
