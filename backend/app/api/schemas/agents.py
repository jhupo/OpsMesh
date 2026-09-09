from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class AgentProfileMutableFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2_000)
    instructions: str = ""
    model: str = Field(default="gpt-4.1", max_length=120)
    model_provider_credential_id: UUID | None = None
    model_settings: dict[str, object] = Field(default_factory=dict)
    capabilities: dict[str, object] = Field(default_factory=dict)
    skills: dict[str, object] = Field(default_factory=dict)
    tool_policy: dict[str, object] = Field(default_factory=dict)
    runtime_policy: dict[str, object] = Field(default_factory=dict)
    memory_policy: dict[str, object] = Field(default_factory=dict)
    approval_policy: dict[str, object] = Field(default_factory=dict)


class AgentProfileCreateRequest(AgentProfileMutableFields):
    pass


class AgentProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    role: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2_000)
    instructions: str | None = None
    model: str | None = Field(default=None, max_length=120)
    model_provider_credential_id: UUID | None = None
    model_settings: dict[str, object] | None = None
    capabilities: dict[str, object] | None = None
    skills: dict[str, object] | None = None
    tool_policy: dict[str, object] | None = None
    runtime_policy: dict[str, object] | None = None
    memory_policy: dict[str, object] | None = None
    approval_policy: dict[str, object] | None = None


class AgentProfileCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    role: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2_000)
    instructions: str | None = None
    model: str | None = Field(default=None, max_length=120)
    model_provider_credential_id: UUID | None = None
    model_settings: dict[str, object] | None = None
    capabilities: dict[str, object] | None = None
    skills: dict[str, object] | None = None
    tool_policy: dict[str, object] | None = None
    runtime_policy: dict[str, object] | None = None
    memory_policy: dict[str, object] | None = None
    approval_policy: dict[str, object] | None = None


class AgentProfileRollbackRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1_000)


class AgentProfileResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    role: str
    description: str
    instructions: str
    model: str
    model_provider_credential_id: UUID | None
    model_provider: dict[str, object] = Field(default_factory=dict)
    model_settings: dict[str, object]
    capabilities: dict[str, object]
    skills: dict[str, object]
    tool_policy: dict[str, object]
    runtime_policy: dict[str, object]
    memory_policy: dict[str, object]
    approval_policy: dict[str, object]
    version: int
    status: str

    @field_serializer(
        "model_settings",
        "model_provider",
        "capabilities",
        "skills",
        "tool_policy",
        "runtime_policy",
        "memory_policy",
        "approval_policy",
    )
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentProfileVersionResponse(ORMModel):
    id: UUID
    created_at: datetime
    workspace_id: UUID
    agent_profile_id: UUID
    version: int
    snapshot: dict[str, object]
    changed_by_user_id: UUID | None = None
    change_reason: str | None = None

    @field_serializer("snapshot")
    def _serialize_snapshot(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentSessionSummaryResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    session_key: str
    scope_type: str
    scope_id: str
    status: str
    agent_profile_id: UUID | None
    agent_team_id: UUID | None
    task_id: UUID | None
    openai_conversation_id: str | None
    metadata: dict[str, object]
    item_count: int
    latest_item_metadata: dict[str, object] | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("metadata", "latest_item_metadata")
    def _serialize_session_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentSessionItemResponse(ORMModel):
    id: UUID
    sequence: int
    item: dict[str, object]
    created_at: datetime
    metadata: dict[str, object]

    @field_serializer("item", "metadata")
    def _serialize_item_payload(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentSessionDetailResponse(ORMModel):
    session: AgentSessionSummaryResponse
    items: list[AgentSessionItemResponse]
    item_limit: int
    item_offset: int


class AgentSessionClearResponse(BaseModel):
    deleted_item_count: int
