from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, model_validator

from backend.app.api.schemas.common import TimestampedModel
from backend.app.core.security.redaction import redact_sensitive_payload

KnowledgeSourceType = Literal["url", "workspace_file"]


class KnowledgeSourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    source_type: KnowledgeSourceType
    uri: str | None = Field(default=None, max_length=2_048)
    workspace_file_id: UUID | None = None
    config: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_reference(self) -> KnowledgeSourceCreateRequest:
        if self.source_type == "url" and self.workspace_file_id is not None:
            raise ValueError("URL knowledge source cannot include workspace_file_id")
        if self.source_type == "workspace_file" and self.uri is not None:
            raise ValueError("Workspace-file knowledge source cannot include uri")
        return self


class KnowledgeSourceUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    source_type: KnowledgeSourceType | None = None
    uri: str | None = Field(default=None, max_length=2_048)
    workspace_file_id: UUID | None = None
    config: dict[str, object] | None = None
    status: Literal["active", "paused"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> KnowledgeSourceUpdateRequest:
        if not any(
            value is not None
            for value in (
                self.name,
                self.description,
                self.source_type,
                self.uri,
                self.workspace_file_id,
                self.config,
                self.status,
            )
        ):
            raise ValueError("At least one knowledge source field is required")
        if self.source_type == "url" and self.workspace_file_id is not None:
            raise ValueError("URL knowledge source cannot include workspace_file_id")
        if self.source_type == "workspace_file" and self.uri is not None:
            raise ValueError("Workspace-file knowledge source cannot include uri")
        return self


class KnowledgeSourceStatusRequest(BaseModel):
    expected_version: int = Field(ge=1)


class KnowledgeSourceResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    description: str
    source_type: KnowledgeSourceType
    uri: str | None
    workspace_file_id: UUID | None
    source_config: dict[str, object]
    source_fingerprint: str
    version: int
    status: str
    last_ingested_at: datetime | None
    last_error_code: str | None

    @field_serializer("source_config")
    def _serialize_source_config(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class KnowledgeSourceIngestionResponse(TimestampedModel):
    workspace_id: UUID
    source_id: UUID
    requested_by_user_id: UUID | None
    source_version: int
    status: Literal["pending", "processing", "succeeded", "failed"]
    attempts: int
    content_sha256: str | None
    byte_count: int
    chunk_count: int
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None


class KnowledgeSourceListResponse(BaseModel):
    items: list[KnowledgeSourceResponse]
    total: int
    limit: int
    offset: int
