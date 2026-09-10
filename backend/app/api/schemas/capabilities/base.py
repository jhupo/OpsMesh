from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from backend.app.api.schemas.common import TimestampedModel


class CapabilityCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    description: str = ""
    default_policy: dict[str, object] = Field(default_factory=dict)


class CapabilityUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    default_policy: dict[str, object] | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")

    @model_validator(mode="after")
    def require_change(self) -> "CapabilityUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one capability field is required")
        null_fields = sorted(
            field for field in self.model_fields_set if getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Capability fields cannot be null: {', '.join(null_fields)}")
        return self


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


class SkillUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    capability_keys: list[str] | None = None
    manifest: dict[str, object] | None = None
    visibility: str | None = Field(default=None, pattern="^(private|public)$")
    status: str | None = Field(default=None, pattern="^(active|disabled)$")

    @model_validator(mode="after")
    def require_change(self) -> "SkillUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one skill field is required")
        null_fields = sorted(
            field for field in self.model_fields_set if getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Skill fields cannot be null: {', '.join(null_fields)}")
        return self


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


class ToolGroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    tool_names: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")

    @model_validator(mode="after")
    def require_change(self) -> "ToolGroupUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one tool group field is required")
        null_fields = sorted(
            field for field in self.model_fields_set if getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Tool group fields cannot be null: {', '.join(null_fields)}")
        return self


class ToolGroupResponse(TimestampedModel):
    key: str
    name: str
    description: str
    tool_names: list[str]
    status: str
