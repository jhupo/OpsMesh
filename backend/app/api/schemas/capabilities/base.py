from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import TimestampedModel


class CapabilityCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    description: str = ""
    default_policy: dict[str, object] = Field(default_factory=dict)


class CapabilityResponse(TimestampedModel):
    key: str
    name: str
    category: str
    description: str
    default_policy: dict[str, object]
    status: str


class SkillCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(default="1.0.0", max_length=80)
    description: str = ""
    capability_keys: list[str] = Field(default_factory=list)
    manifest: dict[str, object] = Field(default_factory=dict)
    visibility: str = Field(default="public", pattern="^(private|public)$")


class SkillResponse(TimestampedModel):
    key: str
    name: str
    version: str
    description: str
    capability_keys: list[str]
    manifest: dict[str, object]
    owner_workspace_id: UUID | None
    visibility: str
    status: str


class ToolGroupCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    tool_names: list[str] = Field(default_factory=list)


class ToolGroupResponse(TimestampedModel):
    key: str
    name: str
    description: str
    tool_names: list[str]
    status: str
