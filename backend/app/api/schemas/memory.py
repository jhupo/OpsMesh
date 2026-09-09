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


class HybridMemoryRetrievalPolicyRequest(BaseModel):
    full_text_weight: float = Field(default=1.0, gt=0, le=10)
    vector_weight: float = Field(default=1.0, gt=0, le=10)
    lexical_weight: float = Field(default=0.35, gt=0, le=10)
    reciprocal_rank_constant: int = Field(default=60, ge=1, le=1_000)
    candidate_multiplier: int = Field(default=4, ge=1, le=20)
    importance_weight: float = Field(default=0.15, ge=0, le=1)
    recency_weight: float = Field(default=0.1, ge=0, le=1)
    recency_half_life_days: int = Field(default=30, ge=1, le=3_650)


class MemoryLifecyclePolicyRequest(BaseModel):
    episodic_decay_half_life_days: int = Field(default=90, ge=1, le=3_650)
    semantic_decay_half_life_days: int = Field(default=365, ge=1, le=7_300)
    archive_expired_episodes: bool = True
    semantic_archive_after_days: int | None = Field(default=None, ge=30, le=7_300)
    auto_promote_episodes: bool = False
    promotion_min_importance: int = Field(default=75, ge=0, le=100)
    promotion_min_access_count: int = Field(default=3, ge=1, le=10_000)


class WorkspaceMemoryConfigurationUpdateRequest(BaseModel):
    embedding_enabled: bool = False
    embedding_credential_id: UUID | None = None
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1, max_length=160)
    embedding_dimensions: int = Field(default=1_536, ge=1, le=16_000)
    retrieval_policy: HybridMemoryRetrievalPolicyRequest = Field(
        default_factory=HybridMemoryRetrievalPolicyRequest
    )
    lifecycle_policy: MemoryLifecyclePolicyRequest = Field(
        default_factory=MemoryLifecyclePolicyRequest
    )
    expected_version: int = Field(ge=1)


class WorkspaceMemoryConfigurationResponse(TimestampedModel):
    workspace_id: UUID
    embedding_enabled: bool
    embedding_credential_id: UUID | None
    embedding_model: str
    embedding_dimensions: int
    retrieval_policy: dict[str, object]
    lifecycle_policy: dict[str, object]
    version: int
    updated_by_user_id: UUID | None


class MemoryEmbeddingRetryResponse(BaseModel):
    reset_entries: int


class MemoryRetrievalEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    agent_run_id: UUID | None
    query_fingerprint: str
    query_terms: list[str]
    requested_limit: int
    scope_filters: dict[str, object]
    ranking_policy: dict[str, object]
    backend_evidence: list[dict[str, object]]
    selected: list[dict[str, object]]
    candidate_count: int
    deduplicated_count: int
    created_at: datetime

    @field_serializer("scope_filters", "ranking_policy")
    def _serialize_mapping(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("backend_evidence", "selected")
    def _serialize_items(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class MemoryLifecycleEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    memory_entry_id: UUID
    action: str
    reason_code: str
    policy_version: int
    before: dict[str, object]
    after: dict[str, object]
    created_at: datetime

    @field_serializer("before", "after")
    def _serialize_state(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class MemoryEmbeddingEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    memory_entry_id: UUID
    credential_id: UUID | None
    configuration_version: int
    embedding_generation: int
    provider: str
    model: str
    dimensions: int
    status: str
    input_tokens: int
    error_code: str | None
    created_at: datetime


class MemoryRetrievalEventListResponse(BaseModel):
    items: list[MemoryRetrievalEventResponse]
    total: int
    limit: int
    offset: int


class MemoryLifecycleEventListResponse(BaseModel):
    items: list[MemoryLifecycleEventResponse]
    total: int
    limit: int
    offset: int


class MemoryEmbeddingEventListResponse(BaseModel):
    items: list[MemoryEmbeddingEventResponse]
    total: int
    limit: int
    offset: int
