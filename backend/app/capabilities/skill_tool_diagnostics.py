from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.capabilities.agent_tool_policy import agent_mcp_policy_mode
from backend.app.capabilities.mcp.catalog_service import McpCatalogService
from backend.app.capabilities.mcp.server_rules import (
    credential_status as _credential_status,
)
from backend.app.capabilities.mcp.server_rules import (
    execution_mode as _execution_mode,
)
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    WorkspaceSkillInstall,
)
from backend.app.capabilities.skill_diagnostic_types import (
    AgentSkillDiagnostic,
    AgentToolDiagnostic,
    AgentToolPolicyDiagnostic,
)
from backend.app.capabilities.skill_manifest_tools import (
    agent_installed_skill_ids,
    manifest_mcp_tools,
)
from backend.app.capabilities.skill_tool_availability import (
    WorkspaceSkillToolAvailability,
    skill_tool_availability,
)
from backend.app.core.config import Settings, get_settings


@dataclass(frozen=True)
class WorkspaceSkillAvailability:
    install: WorkspaceSkillInstall
    usable: bool
    required_tools: list[str]
    tools: list[WorkspaceSkillToolAvailability]
    blocked_reasons: list[str]


class SkillToolDiagnosticsService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def workspace_skill_availability(
        self,
        workspace_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillAvailability:
        install = self._require_workspace_install(workspace_id, install_id)
        required_tools = manifest_mcp_tools(install.installed_manifest)
        tool_availability = self.skill_tool_availability_for_tools(
            workspace_id,
            required_tools,
        )
        blocked_reasons: list[str] = []
        if install.status != "active":
            blocked_reasons.append("skill_install_disabled")
        missing_tools = [item.tool_name for item in tool_availability if not item.available]
        if missing_tools:
            blocked_reasons.append("missing_required_mcp_tools")
        return WorkspaceSkillAvailability(
            install=install,
            usable=not blocked_reasons,
            required_tools=required_tools,
            tools=tool_availability,
            blocked_reasons=blocked_reasons,
        )

    def skill_tool_availability_for_tools(
        self,
        workspace_id: UUID,
        required_tools: list[str],
    ) -> list[WorkspaceSkillToolAvailability]:
        allowed_tools = McpCatalogService(
            self._session,
            self._settings,
        ).list_allowed_mcp_tools(workspace_id)
        allowed_by_name: dict[str, tuple[McpToolAllowlist, McpServer]] = {}
        for allow, server in allowed_tools:
            allowed_by_name.setdefault(allow.tool_name, (allow, server))
        credential_counts, workspace_credential_count = self._credential_counts_for_servers(
            workspace_id,
            [server.id for _, server in allowed_by_name.values()],
        )
        return [
            skill_tool_availability(
                tool_name,
                allowed_by_name.get(tool_name),
                credential_count=credential_counts.get(allowed_by_name[tool_name][1].id, 0)
                if tool_name in allowed_by_name
                else 0,
                workspace_credential_count=workspace_credential_count,
                stale_after=self._mcp_health_check_stale_after,
            )
            for tool_name in required_tools
        ]

    def agent_tool_policy_diagnostics(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
    ) -> AgentToolPolicyDiagnostic:
        agent = self._session.get(AgentProfile, agent_profile_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")

        policy_mode, configured_tool_names = agent_mcp_policy_mode(agent)
        configured_tool_set = set(configured_tool_names or [])
        allowed_tools = McpCatalogService(
            self._session,
            self._settings,
        ).list_allowed_mcp_tools(workspace_id)
        allowed_by_name: dict[str, tuple[McpToolAllowlist, McpServer]] = {}
        for allow, server in allowed_tools:
            allowed_by_name.setdefault(allow.tool_name, (allow, server))

        installed_skill_ids = agent_installed_skill_ids(agent)
        installs_by_id = self._workspace_installs_by_id(workspace_id, installed_skill_ids)
        missing_install_ids = [
            install_id for install_id in installed_skill_ids if install_id not in installs_by_id
        ]
        installed_skill_diagnostics = [
            self._agent_skill_diagnostic(workspace_id, installs_by_id[install_id])
            for install_id in installed_skill_ids
            if install_id in installs_by_id
        ]

        required_tool_names = {
            tool_name
            for item in installed_skill_diagnostics
            for tool_name in item["required_tools"]
        }
        candidate_tool_names = (
            set(allowed_by_name) if configured_tool_names is None else configured_tool_set
        ) | required_tool_names
        credential_counts, workspace_credential_count = self._credential_counts_for_servers(
            workspace_id,
            [server.id for _, server in allowed_by_name.values()],
        )
        effective_tools = [
            self._agent_tool_diagnostic(
                tool_name,
                allowed_by_name.get(tool_name),
                allowed_by_agent_policy=(
                    configured_tool_names is None or tool_name in configured_tool_set
                ),
                credential_count=credential_counts.get(
                    allowed_by_name[tool_name][1].id,
                    0,
                )
                if tool_name in allowed_by_name
                else 0,
                workspace_credential_count=workspace_credential_count,
            )
            for tool_name in sorted(candidate_tool_names)
        ]
        missing_policy_tools = sorted(
            configured_tool_set - set(allowed_by_name)
            if configured_tool_names is not None
            else set()
        )
        blocked_reasons: list[str] = []
        if agent.status != "active":
            blocked_reasons.append("agent_inactive")
        if missing_install_ids:
            blocked_reasons.append("missing_agent_skill_installs")
        if missing_policy_tools:
            blocked_reasons.append("configured_mcp_tools_not_allowed")
        if any(not item["usable"] for item in installed_skill_diagnostics):
            blocked_reasons.append("unusable_installed_skills")
        if any(
            tool["allowed_by_agent_policy"] and not tool["available"] for tool in effective_tools
        ):
            blocked_reasons.append("unavailable_allowed_mcp_tools")

        return {
            "workspace_id": workspace_id,
            "agent_profile_id": agent.id,
            "agent_name": agent.name,
            "agent_role": agent.role,
            "agent_status": agent.status,
            "policy_mode": policy_mode,
            "configured_mcp_tools": configured_tool_names,
            "missing_policy_tools": missing_policy_tools,
            "missing_agent_skill_install_ids": missing_install_ids,
            "installed_skills": installed_skill_diagnostics,
            "effective_tools": effective_tools,
            "blocked_reasons": blocked_reasons,
        }

    def workspace_tool_policy_matrix(self, workspace_id: UUID) -> dict[str, object]:
        agents = self._session.scalars(
            select(AgentProfile)
            .where(AgentProfile.workspace_id == workspace_id)
            .order_by(AgentProfile.status.asc(), AgentProfile.name.asc(), AgentProfile.id.asc())
        ).all()
        agent_items = [
            self.agent_tool_policy_diagnostics(workspace_id, agent.id) for agent in agents
        ]
        tool_names = sorted(
            {
                str(tool["tool_name"])
                for agent_item in agent_items
                for tool in agent_item["effective_tools"]
                if isinstance(tool, dict) and tool.get("tool_name") is not None
            }
        )
        blocked_reasons: Counter[str] = Counter()
        policy_modes: Counter[str] = Counter()
        unavailable_tool_count = 0
        for agent_item in agent_items:
            blocked_reasons.update(
                reason for reason in agent_item["blocked_reasons"] if isinstance(reason, str)
            )
            policy_modes.update([str(agent_item["policy_mode"])])
            unavailable_tool_count += sum(
                1
                for tool in agent_item["effective_tools"]
                if isinstance(tool, dict) and tool.get("available") is not True
            )
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "tool_names": tool_names,
            "summary": {
                "agent_count": len(agent_items),
                "blocked_agent_count": sum(
                    1 for agent_item in agent_items if agent_item["blocked_reasons"]
                ),
                "tool_name_count": len(tool_names),
                "unavailable_tool_count": unavailable_tool_count,
                "policy_modes": dict(sorted(policy_modes.items())),
                "blocked_reasons": dict(sorted(blocked_reasons.items())),
            },
            "agents": agent_items,
        }

    def _workspace_installs_by_id(
        self,
        workspace_id: UUID,
        install_ids: list[UUID],
    ) -> dict[UUID, WorkspaceSkillInstall]:
        if not install_ids:
            return {}
        installs = self._session.scalars(
            select(WorkspaceSkillInstall).where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.id.in_(install_ids),
            )
        ).all()
        return {install.id: install for install in installs}

    def _agent_skill_diagnostic(
        self,
        workspace_id: UUID,
        install: WorkspaceSkillInstall,
    ) -> AgentSkillDiagnostic:
        availability = self.workspace_skill_availability(workspace_id, install.id)
        return {
            "install_id": install.id,
            "installed_key": install.installed_key,
            "installed_name": install.installed_name,
            "installed_version": install.installed_version,
            "source_visibility": install.source_visibility,
            "status": install.status,
            "usable": availability.usable,
            "required_tools": availability.required_tools,
            "blocked_reasons": availability.blocked_reasons,
        }

    def _agent_tool_diagnostic(
        self,
        tool_name: str,
        allowed_tool: tuple[McpToolAllowlist, McpServer] | None,
        *,
        allowed_by_agent_policy: bool,
        credential_count: int,
        workspace_credential_count: int,
    ) -> AgentToolDiagnostic:
        availability = skill_tool_availability(
            tool_name,
            allowed_tool,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
            stale_after=self._mcp_health_check_stale_after,
        )
        credential_status: str | None = None
        execution_mode: str | None = None
        if allowed_tool is not None:
            _, server = allowed_tool
            credential_status = _credential_status(
                server,
                credential_count=credential_count,
                workspace_credential_count=workspace_credential_count,
            )
            execution_mode = _execution_mode(server)
        blocked_reasons = list(availability.blocked_reasons)
        if not allowed_by_agent_policy:
            blocked_reasons.append("not_allowed_by_agent_policy")
        return {
            "tool_name": tool_name,
            "allowed_by_agent_policy": allowed_by_agent_policy,
            "allowed_in_workspace": allowed_tool is not None,
            "available": availability.available and allowed_by_agent_policy,
            "server_id": availability.server_id,
            "server_name": availability.server_name,
            "capability_key": availability.capability_key,
            "requires_approval": availability.requires_approval,
            "risk_level": availability.risk_level,
            "credential_status": credential_status,
            "execution_mode": execution_mode,
            "blocked_reasons": blocked_reasons,
        }

    def _credential_counts_for_servers(
        self,
        workspace_id: UUID,
        server_ids: list[UUID],
    ) -> tuple[dict[UUID, int], int]:
        if not server_ids:
            return {}, 0
        credentials = self._session.scalars(
            select(McpCredentialReference).where(
                McpCredentialReference.workspace_id == workspace_id,
                McpCredentialReference.status == "active",
                or_(
                    McpCredentialReference.mcp_server_id.in_(server_ids),
                    McpCredentialReference.mcp_server_id.is_(None),
                ),
            )
        ).all()
        credential_counts: dict[UUID, int] = {server_id: 0 for server_id in server_ids}
        workspace_credential_count = 0
        for credential in credentials:
            if credential.mcp_server_id is None:
                workspace_credential_count += 1
                continue
            if credential.mcp_server_id in credential_counts:
                credential_counts[credential.mcp_server_id] += 1
        return credential_counts, workspace_credential_count

    def _require_workspace_install(
        self,
        workspace_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillInstall:
        install = self._session.get(WorkspaceSkillInstall, install_id)
        if install is None or install.workspace_id != workspace_id:
            raise ValueError("Workspace skill install not found")
        return install

    @property
    def _mcp_health_check_stale_after(self) -> timedelta:
        return timedelta(seconds=self._settings.mcp_health_check_stale_after_seconds)
