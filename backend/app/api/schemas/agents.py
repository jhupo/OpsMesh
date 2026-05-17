from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import TimestampedModel


class AgentProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2_000)
    instructions: str = ""
    model: str = Field(default="gpt-4.1", max_length=120)
    model_settings: dict[str, object] = Field(default_factory=dict)
    capabilities: dict[str, object] = Field(default_factory=dict)
    skills: dict[str, object] = Field(default_factory=dict)
    tool_policy: dict[str, object] = Field(default_factory=dict)
    runtime_policy: dict[str, object] = Field(default_factory=dict)
    memory_policy: dict[str, object] = Field(default_factory=dict)
    approval_policy: dict[str, object] = Field(default_factory=dict)


class AgentProfileResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    role: str
    description: str
    instructions: str
    model: str
    model_settings: dict[str, object]
    capabilities: dict[str, object]
    skills: dict[str, object]
    tool_policy: dict[str, object]
    runtime_policy: dict[str, object]
    memory_policy: dict[str, object]
    approval_policy: dict[str, object]
    version: int
    status: str

