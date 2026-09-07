from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, model_validator

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload

ResourceType = Literal[
    "file_collection",
    "memory_collection",
    "mcp_resource",
    "runtime",
    "external_service",
]
ResourceAccessMode = Literal["read", "write", "read_write", "execute"]


class CapabilityResourceCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=160)
    resource_type: ResourceType
    description: str = Field(default="", max_length=2_000)
    access_mode: ResourceAccessMode = "read"
    locator: dict[str, object]
    parameter_schema: dict[str, object] = Field(default_factory=dict)
    default_parameters: dict[str, object] = Field(default_factory=dict)


class CapabilityResourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    access_mode: ResourceAccessMode | None = None
    locator: dict[str, object] | None = None
    parameter_schema: dict[str, object] | None = None
    default_parameters: dict[str, object] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "CapabilityResourceUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one resource field is required")
        null_fields = sorted(
            field_name
            for field_name in self.model_fields_set
            if getattr(self, field_name) is None
        )
        if null_fields:
            raise ValueError(f"Resource fields cannot be null: {', '.join(null_fields)}")
        return self


class CapabilityResourceResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    key: str
    name: str
    resource_type: str
    description: str
    access_mode: str
    locator: dict[str, object]
    parameter_schema: dict[str, object]
    default_parameters: dict[str, object]
    version: int
    status: str

    @field_serializer("locator", "default_parameters")
    def serialize_configuration(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class CapabilityToolDescriptor(BaseModel):
    name: str
    source: Literal["product", "mcp"]
    description: str
    input_schema: dict[str, object]
    requires_approval: bool
    risk_level: str
    capability_key: str | None = None
    mcp_server_id: UUID | None = None
    mcp_server_name: str | None = None
    policy: dict[str, object] = Field(default_factory=dict)

    @field_serializer("policy")
    def serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceCapabilityCatalogResponse(BaseModel):
    workspace_id: UUID
    tools: list[CapabilityToolDescriptor]
    resources: list[CapabilityResourceResponse]
