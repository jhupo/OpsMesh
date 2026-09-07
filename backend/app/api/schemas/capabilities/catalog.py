from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.capabilities.schema_validation import reject_embedded_secrets

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
            field_name for field_name in self.model_fields_set if getattr(self, field_name) is None
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
    def require_locked_defaults(self) -> "CapabilityParameterPolicy":
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


class TeamCapabilityPolicyUpdateRequest(BaseModel):
    capability_policy: CapabilityTeamPolicy


class TeamCapabilityPolicyResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    capability_policy: CapabilityTeamPolicy
    capability_policy_version: int


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
