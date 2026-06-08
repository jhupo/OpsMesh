from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import (
    is_sensitive_payload_key,
    redact_sensitive_payload,
    redact_sensitive_text,
)
from backend.app.secrets.service import (
    external_vault_reference_metadata,
    hosted_secret_metadata,
    vault_reference_kind,
)


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


class AgentSkillPolicyDiagnosticResponse(BaseModel):
    install_id: UUID
    installed_key: str
    installed_name: str
    installed_version: str
    source_visibility: str
    status: str
    usable: bool
    required_tools: list[str]
    blocked_reasons: list[str] = Field(default_factory=list)


class AgentMcpToolPolicyDiagnosticResponse(BaseModel):
    tool_name: str
    allowed_by_agent_policy: bool
    allowed_in_workspace: bool
    available: bool
    server_id: UUID | None = None
    server_name: str | None = None
    capability_key: str | None = None
    requires_approval: bool = False
    risk_level: str | None = None
    credential_status: str | None = None
    execution_mode: str | None = None
    blocked_reasons: list[str] = Field(default_factory=list)


class AgentToolPolicyDiagnosticsResponse(BaseModel):
    workspace_id: UUID
    agent_profile_id: UUID
    agent_name: str
    agent_role: str
    agent_status: str
    policy_mode: str
    configured_mcp_tools: list[str] | None
    missing_policy_tools: list[str]
    missing_agent_skill_install_ids: list[UUID]
    installed_skills: list[AgentSkillPolicyDiagnosticResponse]
    effective_tools: list[AgentMcpToolPolicyDiagnosticResponse]
    blocked_reasons: list[str] = Field(default_factory=list)


class WorkspaceToolPolicyMatrixResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    tool_names: list[str]
    summary: dict[str, object]
    agents: list[AgentToolPolicyDiagnosticsResponse]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceCapabilityGovernanceSkillResponse(BaseModel):
    install_id: UUID
    installed_key: str
    installed_name: str
    installed_version: str
    status: str
    usable: bool
    required_tools: list[str]
    blocked_reasons: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class WorkspaceCapabilityGovernanceAgentResponse(BaseModel):
    agent_profile_id: UUID
    name: str
    role: str
    status: str
    policy_mode: str
    configured_mcp_tools: list[str] | None
    installed_skill_count: int
    effective_tool_count: int
    unavailable_tool_count: int
    blocked_reasons: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class WorkspaceCapabilityGovernanceMcpServerResponse(BaseModel):
    server_id: UUID
    name: str
    server_type: str
    status: str
    health_status: str
    execution_mode: str
    executable: bool
    credential_status: str
    allowed_tool_count: int
    high_risk_tool_count: int
    approval_required_tool_count: int
    failed_call_count: int
    blocked_reasons: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class WorkspaceCapabilityGovernanceResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    skills: list[WorkspaceCapabilityGovernanceSkillResponse]
    agents: list[WorkspaceCapabilityGovernanceAgentResponse]
    mcp_servers: list[WorkspaceCapabilityGovernanceMcpServerResponse]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceCapabilityGovernanceApplyRequest(BaseModel):
    dry_run: bool = True
    actions: list[str] = Field(default_factory=list, max_length=5)
    install_ids: list[UUID] = Field(default_factory=list, max_length=100)
    mcp_server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    max_items: int = Field(default=50, ge=1, le=200)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class WorkspaceCapabilityGovernanceApplyResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    dry_run: bool
    status: str
    requested_actions: list[str]
    eligible_action_count: int
    applied_count: int
    skipped_count: int
    summary: dict[str, object]
    results: list[dict[str, object]]
    skipped: list[dict[str, object]]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("results", "skipped")
    def _serialize_items(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


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


class McpServerHealthCheckRequest(BaseModel):
    health_status: str = Field(pattern="^(healthy|unhealthy|unknown)$")
    error_code: str | None = Field(default=None, max_length=120)


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

    @field_serializer("connection")
    def _serialize_connection(self, connection: dict[str, object]) -> dict[str, object]:
        return _redacted_connection(connection)

    @field_serializer("last_error")
    def _serialize_last_error(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) if value is not None else None


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

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


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
    external_ref: str = Field(exclude=True, repr=False)
    secret_fingerprint: str | None
    encryption_key_id: str | None
    scopes: list[str]
    status: str

    @computed_field
    @property
    def external_ref_configured(self) -> bool:
        raw_value = getattr(self, "external_ref", None)
        return isinstance(raw_value, str) and bool(raw_value)

    @computed_field
    @property
    def external_ref_kind(self) -> str | None:
        raw_value = getattr(self, "external_ref", None)
        return vault_reference_kind(raw_value) if isinstance(raw_value, str) else None

    @computed_field
    @property
    def secret_metadata(self) -> dict[str, object]:
        if self.secret_fingerprint:
            return hosted_secret_metadata(
                provider="hosted",
                encryption_key_id=self.encryption_key_id,
                secret_fingerprint=self.secret_fingerprint,
            ).to_api_dict()
        raw_value = getattr(self, "external_ref", None)
        external_ref = raw_value if isinstance(raw_value, str) else ""
        return external_vault_reference_metadata(
            provider=self.provider,
            external_ref=external_ref,
        ).to_api_dict()


class McpToolDescriptor(BaseModel):
    server_id: UUID
    server_name: str
    tool_name: str
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class McpCatalogUsageResponse(BaseModel):
    call_count: int
    failed_call_count: int
    last_call_at: datetime | None
    last_call_status: str | None
    last_error_code: str | None


class McpCatalogToolPolicySummaryResponse(BaseModel):
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int
    max_calls_per_run: int | None
    max_calls_per_hour: int | None
    current_hour_call_count: int
    hourly_limit_remaining: int | None
    limit_window_seconds: int


class McpCatalogToolResponse(BaseModel):
    id: UUID
    tool_name: str
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]
    policy_summary: McpCatalogToolPolicySummaryResponse
    status: str
    usage: McpCatalogUsageResponse

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


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
    usage: McpCatalogUsageResponse
    tools: list[McpCatalogToolResponse]
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_error")
    def _serialize_last_error(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) if value is not None else None


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
    task_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    approval_id: UUID | None
    tool_name: str
    status: str
    latency_ms: int | None
    argument_sha256: str | None
    response_sha256: str | None
    error_code: str | None
    request: dict[str, object]
    response: dict[str, object] | None
    error: dict[str, object] | None
    created_at: datetime

    @field_serializer("request")
    def _serialize_request(self, request: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(request)

    @field_serializer("response")
    def _serialize_response(
        self,
        response: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(response) if response is not None else None

    @field_serializer("error")
    def _serialize_error(self, error: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(error) if error is not None else None


_SENSITIVE_CONNECTION_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "headers",
    "password",
    "secret",
    "token",
}


def _redacted_connection(connection: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in connection.items():
        key_text = str(key)
        if _is_sensitive_connection_key(key_text):
            redacted[key_text] = "[redacted]"
            continue
        if key_text in {"url", "endpoint"} and isinstance(value, str):
            redacted[f"{key_text}_configured"] = bool(value)
            redacted[f"{key_text}_host"] = _url_host(value)
            continue
        if isinstance(value, dict):
            redacted[key_text] = _redacted_connection(value)
            continue
        if isinstance(value, list):
            redacted[key_text] = [_redacted_connection_item(item) for item in value]
            continue
        redacted[key_text] = value
    return redacted


def _redacted_connection_item(value: object) -> object:
    if isinstance(value, dict):
        return _redacted_connection(value)
    return value


def _is_sensitive_connection_key(key: str) -> bool:
    return is_sensitive_payload_key(key)


def _url_host(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.netloc or None
