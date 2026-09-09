from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload

SemanticScope = Literal["workspace", "team", "agent"]
SemanticKnowledgeType = Literal["fact", "configuration", "policy", "procedure"]


class SemanticMemoryUpsertRequest(BaseModel):
    scope_type: SemanticScope = "workspace"
    scope_id: UUID | None = None
    memory_key: str = Field(min_length=1, max_length=160)
    knowledge_type: SemanticKnowledgeType = "fact"
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    importance: int = Field(default=50, ge=0, le=100)
    metadata: dict[str, object] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)
    change_reason: str | None = Field(default=None, max_length=1_000)


class SemanticMemoryArchiveRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    change_reason: str | None = Field(default=None, max_length=1_000)


class SemanticMemoryResponse(TimestampedModel):
    workspace_id: UUID
    memory_layer: str
    scope_type: str
    scope_id: str
    memory_key: str | None
    entry_type: str
    title: str
    content: str
    tags: list[str]
    importance: int
    status: str
    revision: int
    content_fingerprint: str
    metadata: dict[str, object] = Field(validation_alias="memory_metadata")
    archived_at: datetime | None

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class SemanticMemoryVersionResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    memory_entry_id: UUID
    revision: int
    snapshot: dict[str, object]
    content_fingerprint: str
    changed_by_user_id: UUID | None
    changed_by_agent_profile_id: UUID | None
    changed_by_agent_run_id: UUID | None
    change_reason: str | None
    created_at: datetime

    @field_serializer("snapshot")
    def _serialize_snapshot(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class SemanticMemoryListResponse(BaseModel):
    items: list[SemanticMemoryResponse]
    total: int
    limit: int
    offset: int
