from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload


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
