from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload


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


class TaskExecutionStatusResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    status: str
    task: dict[str, object]
    control: dict[str, object]
    summary: dict[str, object]
    current_focus: dict[str, object]
    active_runs: list[dict[str, object]]
    blocked_reasons: list[str]
    recommended_actions: list[dict[str, object]]
    recent_messages: list[dict[str, object]]
    recent_events: list[dict[str, object]]
    diagnostics: dict[str, object]

    @field_serializer("task", "control", "summary", "current_focus", "diagnostics")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer(
        "active_runs",
        "recommended_actions",
        "recent_messages",
        "recent_events",
    )
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class TaskInteractionTranscriptResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    participants: list[dict[str, object]]
    items: list[dict[str, object]]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("participants", "items")
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class TaskDeliveryReviewResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    status: str
    task: dict[str, object]
    summary: dict[str, object]
    final_output: dict[str, object] | None
    steps: list[dict[str, object]]
    unattached_artifacts: list[dict[str, object]]
    recommended_actions: list[dict[str, object]]

    @field_serializer("task", "summary", "final_output")
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None

    @field_serializer("steps", "unattached_artifacts", "recommended_actions")
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class TaskControlDiagnosticsResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    status: str
    control: dict[str, object]
    summary: dict[str, object]
    paused_steps: list[dict[str, object]]
    cancelled_runs: list[dict[str, object]]
    scheduled_runs: list[dict[str, object]]
    active_runs: list[dict[str, object]]
    worker_cancel_requests: list[dict[str, object]]
    recent_control_messages: list[dict[str, object]]
    recommended_actions: list[dict[str, object]]

    @field_serializer("control", "summary")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer(
        "paused_steps",
        "cancelled_runs",
        "scheduled_runs",
        "active_runs",
        "worker_cancel_requests",
        "recent_control_messages",
        "recommended_actions",
    )
    def _serialize_metadata_list(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


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
