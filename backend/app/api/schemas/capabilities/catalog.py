from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from backend.app.domains.capabilities.catalog.contracts import (
    CapabilityTeamPolicy,
    ResourceAccessMode,
    ResourceType,
)


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


class TeamCapabilityPolicyUpdateRequest(BaseModel):
    capability_policy: CapabilityTeamPolicy


class TeamCapabilityPolicyResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    capability_policy: CapabilityTeamPolicy
    capability_policy_version: int
