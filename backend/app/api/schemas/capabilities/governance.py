from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload


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
