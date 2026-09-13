from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.capabilities.resources.schema import reject_embedded_secrets

ResourceType = Literal[
    "file_collection",
    "memory_collection",
    "mcp_resource",
    "runtime",
    "external_service",
]
ResourceAccessMode = Literal["read", "write", "read_write", "execute"]


class CapabilityResourceCreatePayload(Protocol):
    key: str
    name: str
    resource_type: ResourceType
    description: str
    access_mode: ResourceAccessMode
    locator: dict[str, object]
    parameter_schema: dict[str, object]
    default_parameters: dict[str, object]


class CapabilityResourceUpdatePayload(Protocol):
    name: str | None
    description: str | None
    access_mode: ResourceAccessMode | None
    locator: dict[str, object] | None
    parameter_schema: dict[str, object] | None
    default_parameters: dict[str, object] | None


class CapabilityResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
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
    mcp_tool_allowlist_id: UUID | None = None
    mcp_server_name: str | None = None
    mcp_server_type: str | None = None
    policy: dict[str, object] = Field(default_factory=dict)
    required_resource_type: ResourceType | None = None
    required_access_modes: list[ResourceAccessMode] = Field(default_factory=list)

    @field_serializer("policy")
    def serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceCapabilityCatalogResponse(BaseModel):
    workspace_id: UUID
    tools: list[CapabilityToolDescriptor]
    resources: list[CapabilityResourceResponse]


class CapabilityParameterPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: dict[str, object] = Field(default_factory=dict)
    locked: list[str] = Field(default_factory=list)

    @field_validator("locked")
    @classmethod
    def validate_locked_fields(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("Locked parameter names cannot be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Locked parameter names cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def require_locked_defaults(self) -> CapabilityParameterPolicy:
        missing = sorted(set(self.locked) - set(self.defaults))
        if missing:
            raise ValueError(f"Locked parameters require defaults: {', '.join(missing)}")
        try:
            reject_embedded_secrets(self.defaults, path="defaults")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class CapabilityPolicyScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_tools: Literal["*"] | list[str] = "*"
    allowed_resource_ids: Literal["*"] | list[UUID] = "*"
    tool_parameters: dict[str, CapabilityParameterPolicy] = Field(default_factory=dict)
    resource_parameters: dict[UUID, CapabilityParameterPolicy] = Field(default_factory=dict)

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: str | list[str]) -> str | list[str]:
        if value == "*":
            return value
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("Allowed tool names cannot be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Allowed tool names cannot contain duplicates")
        return normalized


class CapabilityTeamPolicy(CapabilityPolicyScope):
    departments: dict[str, CapabilityPolicyScope] = Field(default_factory=dict)

    @field_validator("departments")
    @classmethod
    def validate_departments(
        cls,
        value: dict[str, CapabilityPolicyScope],
    ) -> dict[str, CapabilityPolicyScope]:
        if any(not name.strip() or len(name) > 120 for name in value):
            raise ValueError("Department policy names must contain 1 to 120 characters")
        return {name.strip(): policy for name, policy in value.items()}


class EffectiveCapabilityDenial(BaseModel):
    kind: Literal["tool", "resource", "policy"]
    key: str
    reason: str


class EffectiveCapabilityTool(BaseModel):
    descriptor: CapabilityToolDescriptor
    parameters: dict[str, object]
    locked_parameters: list[str]
    provenance: list[str]


class EffectiveCapabilityResource(BaseModel):
    resource: CapabilityResourceResponse
    parameters: dict[str, object]
    locked_parameters: list[str]
    provenance: list[str]


class EffectiveCapabilityCatalogResponse(BaseModel):
    catalog_version: int = 1
    workspace_id: UUID
    agent_profile_id: UUID
    agent_profile_version: int
    team_id: UUID | None
    team_policy_version: int | None
    team_member_id: UUID | None
    department: str | None
    tools: list[EffectiveCapabilityTool]
    resources: list[EffectiveCapabilityResource]
    denied: list[EffectiveCapabilityDenial]
    fingerprint: str
