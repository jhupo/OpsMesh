from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.core.security.redaction import (
    redact_sensitive_payload,
    redact_sensitive_payload_item,
)


class ModelProviderOperationsCredentialResponse(BaseModel):
    id: UUID
    name: str
    provider: str
    default_model: str
    model_api: str | None
    model_apis: list[str]
    status: str
    health_status: str
    failure_count: int
    last_failure_code: str | None
    last_failure_message: str | None
    selectable: bool
    not_selectable_reasons: list[str]
    budget: dict[str, object] = Field(default_factory=dict)
    provenance: dict[str, object] = Field(default_factory=dict)

    @field_serializer("last_failure_message", "budget", "provenance")
    def _serialize_sensitive_fields(
        self,
        value: str | dict[str, object] | None,
    ) -> str | dict[str, object] | None:
        if isinstance(value, dict):
            return redact_sensitive_payload(value)
        return value


class ModelProviderOperationsAgentResponse(BaseModel):
    id: UUID
    name: str
    role: str
    status: str
    model: str
    provider: str | None
    protocol: str | None
    model_api: str | None
    credential_id: UUID | None
    readiness_status: str
    reasons: list[str]
    warnings: list[str]
    failure: dict[str, object] = Field(default_factory=dict)
    budget: dict[str, object] = Field(default_factory=dict)
    provenance: dict[str, object] = Field(default_factory=dict)

    @field_serializer("failure", "budget", "provenance")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class ModelProviderOperationsRunResponse(BaseModel):
    run_id: UUID
    task_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    status: str
    model: str | None
    provider: str | None
    protocol: str | None
    credential_id: UUID | None
    provider_snapshot_source: str
    fallback_status: str
    fallback: dict[str, object] = Field(default_factory=dict)
    failure: dict[str, object] = Field(default_factory=dict)
    provenance: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_serializer("fallback", "failure", "provenance")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class ModelProviderOperationsResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    summary: dict[str, object] = Field(default_factory=dict)
    credentials: list[ModelProviderOperationsCredentialResponse]
    agents: list[ModelProviderOperationsAgentResponse]
    runs: list[ModelProviderOperationsRunResponse]
    fallback: dict[str, object] = Field(default_factory=dict)
    suggested_actions: list[dict[str, object]] = Field(default_factory=list)

    @field_serializer("summary", "fallback", "suggested_actions")
    def _serialize_payload(self, value: object) -> object:
        return redact_sensitive_payload_item(value)
