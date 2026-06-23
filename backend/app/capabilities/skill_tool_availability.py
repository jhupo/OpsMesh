from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from backend.app.capabilities.mcp_policy import mcp_health_check_stale
from backend.app.capabilities.mcp_server_rules import (
    credential_status as _credential_status,
)
from backend.app.capabilities.mcp_server_rules import (
    execution_mode as _execution_mode,
)
from backend.app.capabilities.models import McpServer, McpToolAllowlist


@dataclass(frozen=True)
class WorkspaceSkillToolAvailability:
    tool_name: str
    available: bool
    server_id: UUID | None
    server_name: str | None
    capability_key: str | None
    requires_approval: bool
    risk_level: str | None
    blocked_reasons: list[str]


def skill_tool_availability(
    tool_name: str,
    allowed_tool: tuple[McpToolAllowlist, McpServer] | None,
    *,
    credential_count: int,
    workspace_credential_count: int,
    stale_after: timedelta,
) -> WorkspaceSkillToolAvailability:
    if allowed_tool is None:
        return WorkspaceSkillToolAvailability(
            tool_name=tool_name,
            available=False,
            server_id=None,
            server_name=None,
            capability_key=None,
            requires_approval=False,
            risk_level=None,
            blocked_reasons=["tool_not_allowed"],
        )
    allow, server = allowed_tool
    blocked_reasons: list[str] = []
    if server.health_status == "unhealthy":
        blocked_reasons.append("server_unhealthy")
    if mcp_health_check_stale(server, stale_after=stale_after):
        blocked_reasons.append("health_check_stale")
    credential_status = _credential_status(
        server,
        credential_count=credential_count,
        workspace_credential_count=workspace_credential_count,
    )
    if credential_status == "missing_required":
        blocked_reasons.append("missing_required_credentials")
    execution_mode = _execution_mode(server)
    if execution_mode == "unsupported":
        blocked_reasons.append("unsupported_server_type")
    return WorkspaceSkillToolAvailability(
        tool_name=tool_name,
        available=not blocked_reasons,
        server_id=server.id,
        server_name=server.name,
        capability_key=allow.capability_key,
        requires_approval=allow.requires_approval,
        risk_level=allow.risk_level,
        blocked_reasons=blocked_reasons,
    )
