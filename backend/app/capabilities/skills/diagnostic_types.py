from typing import TypedDict
from uuid import UUID


class AgentSkillDiagnostic(TypedDict):
    install_id: UUID
    installed_key: str
    installed_name: str
    installed_version: str
    source_visibility: str
    status: str
    usable: bool
    required_tools: list[str]
    blocked_reasons: list[str]


class AgentToolDiagnostic(TypedDict):
    tool_name: str
    allowed_by_agent_policy: bool
    allowed_in_workspace: bool
    available: bool
    server_id: UUID | None
    server_name: str | None
    capability_key: str | None
    requires_approval: bool
    risk_level: str | None
    credential_status: str | None
    execution_mode: str | None
    blocked_reasons: list[str]


class AgentToolPolicyDiagnostic(TypedDict):
    workspace_id: UUID
    agent_profile_id: UUID
    agent_name: str
    agent_role: str
    agent_status: str
    policy_mode: str
    configured_mcp_tools: list[str] | None
    missing_policy_tools: list[str]
    missing_agent_skill_install_ids: list[UUID]
    installed_skills: list[AgentSkillDiagnostic]
    effective_tools: list[AgentToolDiagnostic]
    blocked_reasons: list[str]
