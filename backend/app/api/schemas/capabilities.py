from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import ORMModel, TimestampedModel


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


class WorkspaceSkillInstallRequest(BaseModel):
    skill_id: UUID
    config: dict[str, object] = Field(default_factory=dict)


class WorkspaceSkillUpgradeRequest(BaseModel):
    skill_id: UUID
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


class McpServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    server_type: str = Field(default="stdio", max_length=80)
    connection: dict[str, object] = Field(default_factory=dict)
    visibility: str = Field(default="private", pattern="^(private|public)$")


class McpServerResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    server_type: str
    connection: dict[str, object]
    visibility: str
    status: str
    health_status: str
    last_health_check_at: datetime | None
    last_error: str | None


class McpToolAllowRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=160)
    capability_key: str | None = Field(default=None, max_length=120)
    requires_approval: bool = False
    risk_level: str = Field(default="low", max_length=32)
    policy: dict[str, object] = Field(default_factory=dict)


class McpToolAllowResponse(TimestampedModel):
    workspace_id: UUID
    mcp_server_id: UUID
    tool_name: str
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]
    status: str


class McpCredentialReferenceCreateRequest(BaseModel):
    mcp_server_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=80)
    external_ref: str = Field(default="", max_length=512)
    secret_payload: dict[str, object] | None = None
    scopes: list[str] = Field(default_factory=list)


class McpCredentialReferenceResponse(TimestampedModel):
    workspace_id: UUID
    mcp_server_id: UUID | None
    name: str
    provider: str
    external_ref: str
    secret_fingerprint: str | None
    encryption_key_id: str | None
    scopes: list[str]
    status: str


class McpToolDescriptor(BaseModel):
    server_id: UUID
    server_name: str
    tool_name: str
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]


class McpCatalogToolResponse(BaseModel):
    id: UUID
    tool_name: str
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]
    status: str


class McpCatalogServerResponse(BaseModel):
    id: UUID
    name: str
    server_type: str
    visibility: str
    status: str
    health_status: str
    last_health_check_at: datetime | None
    last_error: str | None
    execution_mode: str
    executable: bool
    blocked_reasons: list[str]
    credential_status: str
    credential_count: int
    workspace_credential_count: int
    connection_summary: dict[str, object]
    tools: list[McpCatalogToolResponse]
    created_at: datetime
    updated_at: datetime


class McpToolCallLogRequest(BaseModel):
    mcp_server_id: UUID | None = None
    agent_run_id: UUID | None = None
    approval_id: UUID | None = None
    tool_name: str = Field(min_length=1, max_length=160)
    status: str = Field(min_length=1, max_length=32)
    request: dict[str, object] = Field(default_factory=dict)
    response: dict[str, object] | None = None
    error: dict[str, object] | None = None


class McpToolCallLogResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    mcp_server_id: UUID | None
    agent_run_id: UUID | None
    approval_id: UUID | None
    tool_name: str
    status: str
    request: dict[str, object]
    response: dict[str, object] | None
    error: dict[str, object] | None
    created_at: datetime
