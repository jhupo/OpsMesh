from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class WorkspaceSkillInstallRequest(BaseModel):
    skill_id: UUID
    config: dict[str, object] = Field(default_factory=dict)


class WorkspaceSkillInstallConfigRequest(BaseModel):
    config: dict[str, object] = Field(default_factory=dict)


class WorkspaceSkillUpgradeRequest(BaseModel):
    skill_id: UUID
    config: dict[str, object] | None = None


class WorkspaceSkillRollbackRequest(BaseModel):
    skill_id: UUID | None = None
    config: dict[str, object] | None = None


class WorkspaceSkillInstallResponse(TimestampedModel):
    workspace_id: UUID
    skill_id: UUID
    installed_by_user_id: UUID | None
    installed_key: str
    installed_name: str
    installed_version: str
    installed_description: str
    installed_capability_keys: list[str]
    installed_manifest: dict[str, object]
    source_owner_workspace_id: UUID | None
    source_visibility: str
    source_checksum: str
    config: dict[str, object]
    status: str
    disabled_at: datetime | None

    @field_serializer("installed_manifest")
    def _serialize_manifest(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("config")
    def _serialize_config(self, value: dict[str, object]) -> dict[str, object]:
        redacted = redact_sensitive_payload(value)
        redacted.pop("_lifecycle", None)
        return redacted


class WorkspaceSkillToolAvailabilityResponse(BaseModel):
    tool_name: str
    available: bool
    server_id: UUID | None = None
    server_name: str | None = None
    capability_key: str | None = None
    requires_approval: bool = False
    risk_level: str | None = None
    blocked_reasons: list[str] = Field(default_factory=list)


class WorkspaceSkillAvailabilityResponse(BaseModel):
    install_id: UUID
    installed_key: str
    status: str
    usable: bool
    required_tools: list[str]
    tools: list[WorkspaceSkillToolAvailabilityResponse]
    blocked_reasons: list[str] = Field(default_factory=list)


class WorkspaceSkillImpactAgentResponse(BaseModel):
    agent_profile_id: UUID
    name: str
    role: str
    status: str
    policy_mode: str
    configured_mcp_tools: list[str] | None


class WorkspaceSkillImpactResponse(BaseModel):
    install_id: UUID
    installed_key: str
    current_version: str
    target_skill_id: UUID | None = None
    target_version: str | None = None
    status: str
    affected_agent_count: int
    affected_agents: list[WorkspaceSkillImpactAgentResponse]
    current_required_tools: list[str]
    target_required_tools: list[str]
    added_required_tools: list[str]
    removed_required_tools: list[str]
    target_tool_availability: list[WorkspaceSkillToolAvailabilityResponse]
    blocked_reasons: list[str] = Field(default_factory=list)
