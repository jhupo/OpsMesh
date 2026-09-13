from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from backend.app.core.security.redaction import redact_sensitive_payload


class AgentProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
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
