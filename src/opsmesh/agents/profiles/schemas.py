from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from opsmesh.shared.contracts import FromAttributesContract
from opsmesh.shared.security.redaction import redact_sensitive_payload


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


class AgentProfileVersionResponse(FromAttributesContract):
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
