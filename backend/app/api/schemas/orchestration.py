from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class OrchestrationApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orchestration_definition_id: UUID
    orchestration_version: int | None = Field(default=None, ge=1)
    enqueue: bool = False


class OrchestrationDefinitionResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    key: str
    name: str
    description: str
    definition: dict[str, object]
    version: int
    status: str
    published_at: datetime | None

    @field_serializer("definition")
    def _serialize_definition(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class OrchestrationRevisionResponse(TimestampedModel):
    workspace_id: UUID
    definition_id: UUID
    version: int
    name: str
    definition: dict[str, object]

    @field_serializer("definition")
    def _serialize_definition(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class OrchestrationValidationResponse(BaseModel):
    workspace_id: UUID
    orchestration_definition_id: UUID
    version: int
    valid: bool
    errors: list[str]
    definition: dict[str, object]

    @field_serializer("definition")
    def _serialize_definition(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
