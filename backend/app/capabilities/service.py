import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TypeVar
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.capabilities import (
    CapabilityCreateRequest,
    McpCredentialReferenceCreateRequest,
    McpServerCreateRequest,
    McpServerHealthCheckRequest,
    McpToolAllowRequest,
    McpToolCallLogRequest,
    SkillCreateRequest,
    ToolGroupCreateRequest,
    WorkspaceSkillInstallRequest,
    WorkspaceSkillRollbackRequest,
    WorkspaceSkillUpgradeRequest,
)
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import (
    Capability,
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
    Skill,
    ToolGroup,
    WorkspaceSkillInstall,
)
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.runs.models import AgentRun
from backend.app.secrets.service import SecretEncryptionService

T = TypeVar("T")
MCP_HEALTH_CHECK_STALE_AFTER = timedelta(hours=24)
MCP_LIMIT_COUNTED_STATUSES = (
    "completed",
    "failed",
    "waiting_approval",
    "waiting_self_hosted",
)
GOVERNANCE_APPLY_ACTIONS = {
    "disable_unusable_skill_installs",
    "disable_blocked_mcp_servers",
    "refresh_mcp_health_check",
}
DEFAULT_GOVERNANCE_APPLY_ACTIONS = [
    "disable_unusable_skill_installs",
    "disable_blocked_mcp_servers",
]
SKILL_INSTALL_GOVERNANCE_DISABLE_REASONS = {
    "missing_required_mcp_tools",
}
MCP_SERVER_GOVERNANCE_DISABLE_REASONS = {
    "health_check_stale",
    "missing_remote_url",
    "missing_stdio_command",
    "server_unhealthy",
    "unsupported_hosted_transport",
    "unsupported_server_type",
}


@dataclass(frozen=True)
class McpCatalogUsage:
    call_count: int
    failed_call_count: int
    last_call_at: datetime | None
    last_call_status: str | None
    last_error_code: str | None


@dataclass(frozen=True)
class McpCatalogTool:
    allowlist: McpToolAllowlist
    usage: McpCatalogUsage
    policy_summary: dict[str, object]


@dataclass(frozen=True)
class McpCatalogServer:
    server: McpServer
    tools: list[McpCatalogTool]
    credential_count: int
    workspace_credential_count: int
    credential_status: str
    execution_mode: str
    executable: bool
    blocked_reasons: list[str]
    connection_summary: dict[str, object]
    usage: McpCatalogUsage


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


@dataclass(frozen=True)
class WorkspaceSkillAvailability:
    install: WorkspaceSkillInstall
    usable: bool
    required_tools: list[str]
    tools: list[WorkspaceSkillToolAvailability]
    blocked_reasons: list[str]


class CapabilityService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService | None = None,
    ) -> None:
        self._session = session
        self._secret_service = secret_service

    def list_capabilities(
        self,
        page: PageParams,
        category: str | None = None,
    ) -> tuple[list[Capability], int]:
        statement = select(Capability).where(Capability.status == "active")
        if category is not None:
            statement = statement.where(Capability.category == category)
        return self._page(statement.order_by(Capability.category.asc(), Capability.key.asc()), page)

    def create_capability(self, data: CapabilityCreateRequest) -> Capability:
        capability = Capability(**data.model_dump())
        self._session.add(capability)
        commit_or_raise_conflict(self._session, "Capability key already exists")
        self._session.refresh(capability)
        return capability

    def list_skills(
        self,
        page: PageParams,
        workspace_id: UUID | None = None,
    ) -> tuple[list[Skill], int]:
        statement = select(Skill).where(Skill.status == "active")
        if workspace_id is not None:
            statement = statement.where(
                or_(
                    Skill.visibility == "public",
                    Skill.owner_workspace_id == workspace_id,
                )
            )
        else:
            statement = statement.where(Skill.visibility == "public")
        statement = statement.order_by(Skill.key.asc())
        return self._page(statement, page)

    def create_skill(self, data: SkillCreateRequest, workspace_id: UUID | None = None) -> Skill:
        owner_workspace_id = workspace_id if data.visibility == "private" else None
        skill = Skill(owner_workspace_id=owner_workspace_id, **data.model_dump())
        self._session.add(skill)
        commit_or_raise_conflict(self._session, "Skill version already exists")
        self._session.refresh(skill)
        return skill

    def install_skill(
        self,
        workspace_id: UUID,
        user_id: UUID,
        data: WorkspaceSkillInstallRequest,
    ) -> WorkspaceSkillInstall:
        return self.install_skill_by_id(
            workspace_id=workspace_id,
            user_id=user_id,
            skill_id=data.skill_id,
            config=data.config,
        )

    def install_skill_by_id(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        skill_id: UUID,
        config: dict[str, object] | None = None,
    ) -> WorkspaceSkillInstall:
        skill = self._require_installable_skill(workspace_id, skill_id)
        install = WorkspaceSkillInstall(
            workspace_id=workspace_id,
            skill_id=skill.id,
            installed_by_user_id=user_id,
            config=config or {},
        )
        self._copy_skill_snapshot(install, skill)
        self._session.add(install)
        flush_or_raise_conflict(self._session, "Skill is already installed in workspace")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
                action="skill.installed",
                target_type="workspace_skill_install",
                target_id=install.id,
                metadata={
                    "skill_id": str(skill.id),
                    "installed_key": install.installed_key,
                    "installed_version": install.installed_version,
                    "source_checksum": install.source_checksum,
                },
            )
        commit_or_raise_conflict(self._session, "Skill is already installed in workspace")
        self._session.refresh(install)
        return install

    def upgrade_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
        data: WorkspaceSkillUpgradeRequest,
    ) -> WorkspaceSkillInstall:
        install = self._require_workspace_install(workspace_id, install_id)
        skill = self._require_installable_skill(workspace_id, data.skill_id)
        _require_same_skill_key(install, skill)
        lifecycle_config = _append_skill_install_history(
            install.config,
            install,
            action="upgrade",
            user_id=user_id,
        )
        install.skill_id = skill.id
        self._copy_skill_snapshot(install, skill)
        if data.config is not None:
            install.config = _config_with_lifecycle(data.config, lifecycle_config)
        else:
            install.config = lifecycle_config
        install.status = "active"
        install.disabled_at = None
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.upgraded",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(skill.id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def rollback_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
        data: WorkspaceSkillRollbackRequest,
    ) -> WorkspaceSkillInstall:
        install = self._require_workspace_install(workspace_id, install_id)
        target_skill_id = data.skill_id or _latest_history_skill_id(install.config)
        if target_skill_id is None:
            raise ValueError("Workspace skill install has no rollback history")
        skill = self._require_installable_skill(workspace_id, target_skill_id)
        _require_same_skill_key(install, skill)
        lifecycle_config = _append_skill_install_history(
            install.config,
            install,
            action="rollback",
            user_id=user_id,
        )
        install.skill_id = skill.id
        self._copy_skill_snapshot(install, skill)
        if data.config is not None:
            install.config = _config_with_lifecycle(data.config, lifecycle_config)
        else:
            install.config = lifecycle_config
        install.status = "active"
        install.disabled_at = None
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.rolled_back",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(skill.id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def disable_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillInstall:
        install = self._require_workspace_install(workspace_id, install_id)
        install.status = "disabled"
        install.disabled_at = datetime.now(UTC)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.disabled",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(install.skill_id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
                "disabled_at": install.disabled_at.isoformat(),
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def workspace_skill_impact(
        self,
        workspace_id: UUID,
        install_id: UUID,
        target_skill_id: UUID | None = None,
    ) -> dict[str, object]:
        install = self._require_workspace_install(workspace_id, install_id)
        target_skill = (
            self._require_installable_skill(workspace_id, target_skill_id)
            if target_skill_id is not None
            else None
        )
        if target_skill is not None:
            _require_same_skill_key(install, target_skill)

        current_required_tools = _manifest_mcp_tools(install.installed_manifest)
        target_required_tools = (
            _manifest_mcp_tools(target_skill.manifest)
            if target_skill is not None
            else current_required_tools
        )
        target_tool_availability = self._skill_tool_availability_for_tools(
            workspace_id,
            target_required_tools,
        )
        affected_agents = self._agents_using_skill_install(workspace_id, install.id)
        blocked_reasons: list[str] = []
        if install.status != "active":
            blocked_reasons.append("skill_install_disabled")
        if any(not item.available for item in target_tool_availability):
            blocked_reasons.append("target_missing_required_mcp_tools")

        return {
            "install_id": install.id,
            "installed_key": install.installed_key,
            "current_version": install.installed_version,
            "target_skill_id": target_skill.id if target_skill is not None else None,
            "target_version": target_skill.version if target_skill is not None else None,
            "status": install.status,
            "affected_agent_count": len(affected_agents),
            "affected_agents": affected_agents,
            "current_required_tools": current_required_tools,
            "target_required_tools": target_required_tools,
            "added_required_tools": sorted(
                set(target_required_tools) - set(current_required_tools)
            ),
            "removed_required_tools": sorted(
                set(current_required_tools) - set(target_required_tools)
            ),
            "target_tool_availability": target_tool_availability,
            "blocked_reasons": blocked_reasons,
        }

    def list_workspace_skills(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        include_disabled: bool = False,
    ) -> tuple[list[WorkspaceSkillInstall], int]:
        statement = select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == workspace_id,
        )
        if not include_disabled:
            statement = statement.where(WorkspaceSkillInstall.status == "active")
        statement = statement.order_by(
            WorkspaceSkillInstall.created_at.desc(),
            WorkspaceSkillInstall.id.desc(),
        )
        return self._page(statement, page)

    def workspace_skill_availability(
        self,
        workspace_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillAvailability:
        install = self._require_workspace_install(workspace_id, install_id)
        required_tools = _manifest_mcp_tools(install.installed_manifest)
        tool_availability = self._skill_tool_availability_for_tools(
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

    def _skill_tool_availability_for_tools(
        self,
        workspace_id: UUID,
        required_tools: list[str],
    ) -> list[WorkspaceSkillToolAvailability]:
        allowed_tools = self.list_allowed_mcp_tools(workspace_id)
        allowed_by_name: dict[str, tuple[McpToolAllowlist, McpServer]] = {}
        for allow, server in allowed_tools:
            allowed_by_name.setdefault(allow.tool_name, (allow, server))
        credentials = self._session.scalars(
            select(McpCredentialReference).where(
                McpCredentialReference.workspace_id == workspace_id,
                McpCredentialReference.status == "active",
            )
        ).all()
        workspace_credential_count = sum(
            1 for credential in credentials if credential.mcp_server_id is None
        )
        credential_counts: dict[UUID, int] = {}
        for credential in credentials:
            if credential.mcp_server_id is not None:
                credential_counts[credential.mcp_server_id] = (
                    credential_counts.get(credential.mcp_server_id, 0) + 1
                )
        return [
            _skill_tool_availability(
                tool_name,
                allowed_by_name.get(tool_name),
                credential_count=credential_counts.get(allowed_by_name[tool_name][1].id, 0)
                if tool_name in allowed_by_name
                else 0,
                workspace_credential_count=workspace_credential_count,
            )
            for tool_name in required_tools
        ]

    def agent_tool_policy_diagnostics(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
    ) -> dict[str, object]:
        agent = self._session.get(AgentProfile, agent_profile_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")

        policy_mode, configured_tool_names = self._agent_mcp_policy_mode(agent)
        configured_tool_set = set(configured_tool_names or [])
        allowed_tools = self.list_allowed_mcp_tools(workspace_id)
        allowed_by_name: dict[str, tuple[McpToolAllowlist, McpServer]] = {}
        for allow, server in allowed_tools:
            allowed_by_name.setdefault(allow.tool_name, (allow, server))

        installed_skill_ids = _agent_installed_skill_ids(agent)
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
            tool["allowed_by_agent_policy"] and not tool["available"]
            for tool in effective_tools
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
                reason
                for reason in agent_item["blocked_reasons"]
                if isinstance(reason, str)
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

    def workspace_capability_governance(self, workspace_id: UUID) -> dict[str, object]:
        installs = self._session.scalars(
            select(WorkspaceSkillInstall)
            .where(WorkspaceSkillInstall.workspace_id == workspace_id)
            .order_by(
                WorkspaceSkillInstall.status.asc(),
                WorkspaceSkillInstall.installed_key.asc(),
                WorkspaceSkillInstall.created_at.desc(),
            )
        ).all()
        skill_items: list[dict[str, object]] = []
        skill_blocked_reasons: Counter[str] = Counter()
        for install in installs:
            availability = self.workspace_skill_availability(workspace_id, install.id)
            skill_blocked_reasons.update(availability.blocked_reasons)
            skill_items.append(
                {
                    "install_id": install.id,
                    "installed_key": install.installed_key,
                    "installed_name": install.installed_name,
                    "installed_version": install.installed_version,
                    "status": install.status,
                    "usable": availability.usable,
                    "required_tools": availability.required_tools,
                    "blocked_reasons": availability.blocked_reasons,
                    "recommended_actions": _skill_governance_actions(
                        availability.blocked_reasons
                    ),
                }
            )

        agents = self._session.scalars(
            select(AgentProfile)
            .where(AgentProfile.workspace_id == workspace_id)
            .order_by(AgentProfile.status.asc(), AgentProfile.name.asc(), AgentProfile.id.asc())
        ).all()
        agent_items: list[dict[str, object]] = []
        agent_blocked_reasons: Counter[str] = Counter()
        for agent in agents:
            diagnostics = self.agent_tool_policy_diagnostics(workspace_id, agent.id)
            effective_tools = _dict_list(diagnostics.get("effective_tools"))
            unavailable_tool_count = sum(
                1
                for tool in effective_tools
                if tool.get("allowed_by_agent_policy") is True and tool.get("available") is False
            )
            blocked_reasons = _string_list(diagnostics.get("blocked_reasons"))
            agent_blocked_reasons.update(blocked_reasons)
            agent_items.append(
                {
                    "agent_profile_id": agent.id,
                    "name": agent.name,
                    "role": agent.role,
                    "status": agent.status,
                    "policy_mode": diagnostics["policy_mode"],
                    "configured_mcp_tools": diagnostics["configured_mcp_tools"],
                    "installed_skill_count": len(
                        _dict_list(diagnostics.get("installed_skills"))
                    ),
                    "effective_tool_count": len(effective_tools),
                    "unavailable_tool_count": unavailable_tool_count,
                    "blocked_reasons": blocked_reasons,
                    "recommended_actions": _agent_governance_actions(blocked_reasons),
                }
            )

        catalog_items, catalog_total = self.list_mcp_catalog(
            workspace_id,
            PageParams(limit=10_000, offset=0),
        )
        server_items: list[dict[str, object]] = []
        server_blocked_reasons: Counter[str] = Counter()
        allowed_tool_count = 0
        high_risk_tool_count = 0
        approval_required_tool_count = 0
        failed_tool_call_count = 0
        for item in catalog_items:
            server = item.server
            tool_count = len(item.tools)
            server_high_risk_tools = sum(
                1
                for tool in item.tools
                if tool.allowlist.risk_level.lower().strip() in {"high", "critical"}
            )
            server_approval_tools = sum(
                1 for tool in item.tools if tool.allowlist.requires_approval
            )
            server_blocked_reasons.update(item.blocked_reasons)
            allowed_tool_count += tool_count
            high_risk_tool_count += server_high_risk_tools
            approval_required_tool_count += server_approval_tools
            failed_tool_call_count += item.usage.failed_call_count
            server_items.append(
                {
                    "server_id": server.id,
                    "name": server.name,
                    "server_type": server.server_type,
                    "status": server.status,
                    "health_status": server.health_status,
                    "execution_mode": item.execution_mode,
                    "executable": item.executable,
                    "credential_status": item.credential_status,
                    "allowed_tool_count": tool_count,
                    "high_risk_tool_count": server_high_risk_tools,
                    "approval_required_tool_count": server_approval_tools,
                    "failed_call_count": item.usage.failed_call_count,
                    "blocked_reasons": item.blocked_reasons,
                    "recommended_actions": _mcp_server_governance_actions(
                        item.blocked_reasons,
                        failed_call_count=item.usage.failed_call_count,
                    ),
                }
            )

        blocked_reason_counts = Counter()
        blocked_reason_counts.update(skill_blocked_reasons)
        blocked_reason_counts.update(agent_blocked_reasons)
        blocked_reason_counts.update(server_blocked_reasons)
        recommended_actions = Counter(
            action
            for item in [*skill_items, *agent_items, *server_items]
            for action in _string_list(item.get("recommended_actions"))
        )
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "summary": {
                "skill_installs": len(skill_items),
                "active_skill_installs": sum(
                    1 for item in skill_items if item["status"] == "active"
                ),
                "unusable_skill_installs": sum(
                    1 for item in skill_items if item["usable"] is False
                ),
                "agents": len(agent_items),
                "agents_with_blocks": sum(
                    1 for item in agent_items if item["blocked_reasons"]
                ),
                "mcp_servers": catalog_total,
                "executable_mcp_servers": sum(1 for item in server_items if item["executable"]),
                "blocked_mcp_servers": sum(
                    1 for item in server_items if item["blocked_reasons"]
                ),
                "allowed_mcp_tools": allowed_tool_count,
                "high_risk_mcp_tools": high_risk_tool_count,
                "tools_requiring_approval": approval_required_tool_count,
                "missing_required_credential_servers": sum(
                    1
                    for item in server_items
                    if item["credential_status"] == "missing_required"
                ),
                "failed_mcp_tool_calls": failed_tool_call_count,
                "catalog_truncated": catalog_total > len(catalog_items),
                "blocked_reason_counts": dict(sorted(blocked_reason_counts.items())),
                "recommended_actions": dict(sorted(recommended_actions.items())),
            },
            "skills": skill_items,
            "agents": agent_items,
            "mcp_servers": server_items,
        }

    def apply_workspace_capability_governance_actions(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool = True,
        actions: list[str] | None = None,
        install_ids: list[UUID] | None = None,
        mcp_server_ids: list[UUID] | None = None,
        max_items: int = 50,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        requested_actions = _governance_actions(actions)
        unsupported_actions = sorted(set(requested_actions) - GOVERNANCE_APPLY_ACTIONS)
        if unsupported_actions:
            raise ValueError(f"Unsupported governance action: {unsupported_actions[0]}")

        remaining = max_items
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        if "disable_unusable_skill_installs" in requested_actions and remaining > 0:
            action_results, action_skipped = self._apply_disable_unusable_skill_installs(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                dry_run=dry_run,
                install_ids=set(install_ids or []),
                limit=remaining,
                reason=reason,
            )
            results.extend(action_results)
            skipped.extend(action_skipped)
            remaining -= len(action_results)
        if "disable_blocked_mcp_servers" in requested_actions and remaining > 0:
            action_results, action_skipped = self._apply_disable_blocked_mcp_servers(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                dry_run=dry_run,
                mcp_server_ids=set(mcp_server_ids or []),
                limit=remaining,
                reason=reason,
            )
            results.extend(action_results)
            skipped.extend(action_skipped)
            remaining -= len(action_results)
        if "refresh_mcp_health_check" in requested_actions and remaining > 0:
            action_results, action_skipped = self._apply_refresh_mcp_health_checks(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                dry_run=dry_run,
                mcp_server_ids=set(mcp_server_ids or []),
                limit=remaining,
                reason=reason,
            )
            results.extend(action_results)
            skipped.extend(action_skipped)
            remaining -= len(action_results)

        summary = {
            "disabled_skill_install_count": sum(
                1 for item in results if item["resource_type"] == "workspace_skill_install"
            ),
            "disabled_mcp_server_count": sum(
                1 for item in results if item["resource_type"] == "mcp_server"
            ),
            "requested_mcp_health_check_count": sum(
                1 for item in results if item["resource_type"] == "mcp_server_health_check"
            ),
            "metadata_keys": sorted((metadata or {}).keys()),
        }
        if not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="capability_governance.actions_applied",
                target_type="workspace",
                target_id=workspace_id,
                metadata={
                    "requested_actions": requested_actions,
                    "applied_count": len(results),
                    "skipped_count": len(skipped),
                    "summary": summary,
                    "reason": reason,
                },
            )
            self._session.commit()

        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "applied" if results else "noop",
            "requested_actions": requested_actions,
            "eligible_action_count": len(results),
            "applied_count": 0 if dry_run else len(results),
            "skipped_count": len(skipped),
            "summary": summary,
            "results": results,
            "skipped": skipped,
        }

    def _apply_disable_unusable_skill_installs(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        install_ids: set[UUID],
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        statement = (
            select(WorkspaceSkillInstall)
            .where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.status == "active",
            )
            .order_by(WorkspaceSkillInstall.installed_key.asc(), WorkspaceSkillInstall.id.asc())
        )
        if install_ids:
            statement = statement.where(WorkspaceSkillInstall.id.in_(install_ids))
        installs = self._session.scalars(statement).all()
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for install in installs:
            availability = self.workspace_skill_availability(workspace_id, install.id)
            blocked_reasons = availability.blocked_reasons
            if not _skill_install_should_be_disabled(blocked_reasons):
                skipped.append(
                    _governance_skipped(
                        action="disable_unusable_skill_installs",
                        resource_type="workspace_skill_install",
                        resource_id=install.id,
                        resource_name=install.installed_key,
                        reason="skill_install_not_governance_disabled",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if len(results) >= limit:
                skipped.append(
                    _governance_skipped(
                        action="disable_unusable_skill_installs",
                        resource_type="workspace_skill_install",
                        resource_id=install.id,
                        resource_name=install.installed_key,
                        reason="max_items_reached",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if not dry_run:
                install.status = "disabled"
                install.disabled_at = datetime.now(UTC)
                AuditService(self._session).record_user_action(
                    workspace_id=workspace_id,
                    user_id=actor_user_id,
                    action="capability_governance.skill_install_disabled",
                    target_type="workspace_skill_install",
                    target_id=install.id,
                    metadata={
                        "installed_key": install.installed_key,
                        "blocked_reasons": blocked_reasons,
                        "reason": reason,
                    },
                )
            results.append(
                _governance_result(
                    action="disable_unusable_skill_installs",
                    resource_type="workspace_skill_install",
                    resource_id=install.id,
                    resource_name=install.installed_key,
                    status="would_apply" if dry_run else "applied",
                    blocked_reasons=blocked_reasons,
                )
            )
        return results, skipped

    def _apply_disable_blocked_mcp_servers(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        mcp_server_ids: set[UUID],
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        catalog_items, _ = self.list_mcp_catalog(
            workspace_id,
            PageParams(limit=10_000, offset=0),
        )
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for item in catalog_items:
            server = item.server
            if server.status != "active":
                continue
            if mcp_server_ids and server.id not in mcp_server_ids:
                continue
            blocked_reasons = item.blocked_reasons
            if not _mcp_server_should_be_governance_disabled(blocked_reasons):
                skipped.append(
                    _governance_skipped(
                        action="disable_blocked_mcp_servers",
                        resource_type="mcp_server",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="mcp_server_not_governance_disabled",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if len(results) >= limit:
                skipped.append(
                    _governance_skipped(
                        action="disable_blocked_mcp_servers",
                        resource_type="mcp_server",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="max_items_reached",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if not dry_run:
                server.status = "disabled"
                AuditService(self._session).record_user_action(
                    workspace_id=workspace_id,
                    user_id=actor_user_id,
                    action="capability_governance.mcp_server_disabled",
                    target_type="mcp_server",
                    target_id=server.id,
                    metadata={
                        "name": server.name,
                        "blocked_reasons": blocked_reasons,
                        "reason": reason,
                    },
                )
            results.append(
                _governance_result(
                    action="disable_blocked_mcp_servers",
                    resource_type="mcp_server",
                    resource_id=server.id,
                    resource_name=server.name,
                    status="would_apply" if dry_run else "applied",
                    blocked_reasons=blocked_reasons,
                )
            )
        return results, skipped

    def _apply_refresh_mcp_health_checks(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        mcp_server_ids: set[UUID],
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        catalog_items, _ = self.list_mcp_catalog(
            workspace_id,
            PageParams(limit=10_000, offset=0),
        )
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for item in catalog_items:
            server = item.server
            if server.status != "active":
                continue
            if mcp_server_ids and server.id not in mcp_server_ids:
                continue
            blocked_reasons = item.blocked_reasons
            if not _mcp_server_health_check_should_refresh(blocked_reasons):
                skipped.append(
                    _governance_skipped(
                        action="refresh_mcp_health_check",
                        resource_type="mcp_server_health_check",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="mcp_server_health_check_not_required",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if len(results) >= limit:
                skipped.append(
                    _governance_skipped(
                        action="refresh_mcp_health_check",
                        resource_type="mcp_server_health_check",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="max_items_reached",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if not dry_run:
                server.health_status = "checking"
                server.last_health_check_at = None
                server.last_error = "health_check_refresh_requested"
                AuditService(self._session).record_user_action(
                    workspace_id=workspace_id,
                    user_id=actor_user_id,
                    action="capability_governance.mcp_health_check_refresh_requested",
                    target_type="mcp_server",
                    target_id=server.id,
                    metadata={
                        "name": server.name,
                        "blocked_reasons": blocked_reasons,
                        "reason": reason,
                    },
                )
            results.append(
                _governance_result(
                    action="refresh_mcp_health_check",
                    resource_type="mcp_server_health_check",
                    resource_id=server.id,
                    resource_name=server.name,
                    status="would_apply" if dry_run else "applied",
                    blocked_reasons=blocked_reasons,
                )
            )
        return results, skipped

    def _require_workspace_install(
        self,
        workspace_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillInstall:
        install = self._session.get(WorkspaceSkillInstall, install_id)
        if install is None or install.workspace_id != workspace_id:
            raise ValueError("Workspace skill install not found")
        return install

    def _require_installable_skill(self, workspace_id: UUID, skill_id: UUID) -> Skill:
        skill = self._session.get(Skill, skill_id)
        if (
            skill is None
            or skill.status != "active"
            or not self._can_use_skill(workspace_id, skill)
        ):
            raise ValueError("Skill not found")
        return skill

    def _copy_skill_snapshot(self, install: WorkspaceSkillInstall, skill: Skill) -> None:
        install.installed_key = skill.key
        install.installed_name = skill.name
        install.installed_version = skill.version
        install.installed_description = skill.description
        install.installed_capability_keys = list(skill.capability_keys)
        install.installed_manifest = dict(skill.manifest)
        install.source_owner_workspace_id = skill.owner_workspace_id
        install.source_visibility = skill.visibility
        install.source_checksum = _skill_checksum(skill)

    def create_tool_group(self, data: ToolGroupCreateRequest) -> ToolGroup:
        group = ToolGroup(**data.model_dump())
        self._session.add(group)
        commit_or_raise_conflict(self._session, "Tool group key already exists")
        self._session.refresh(group)
        return group

    def list_tool_groups(self, page: PageParams) -> tuple[list[ToolGroup], int]:
        statement = (
            select(ToolGroup)
            .where(ToolGroup.status == "active")
            .order_by(ToolGroup.key.asc())
        )
        return self._page(statement, page)

    def create_mcp_server(
        self,
        workspace_id: UUID,
        data: McpServerCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpServer:
        server = McpServer(workspace_id=workspace_id, **data.model_dump())
        self._session.add(server)
        flush_or_raise_conflict(self._session, "MCP server name already exists")
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.created",
                target_type="mcp_server",
                target_id=server.id,
                metadata={"name": server.name, "server_type": server.server_type},
            )
        commit_or_raise_conflict(self._session, "MCP server name already exists")
        self._session.refresh(server)
        return server

    def list_mcp_servers(self, workspace_id: UUID, page: PageParams) -> tuple[list[McpServer], int]:
        statement = (
            select(McpServer)
            .where(McpServer.workspace_id == workspace_id)
            .order_by(McpServer.created_at.desc())
        )
        return self._page(statement, page)

    def allow_mcp_tool(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        data: McpToolAllowRequest,
        actor_user_id: UUID | None = None,
    ) -> McpToolAllowlist:
        self._require_server(workspace_id, mcp_server_id)
        allow = McpToolAllowlist(
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
            **data.model_dump(),
        )
        self._session.add(allow)
        flush_or_raise_conflict(self._session, "MCP tool is already allowed for this server")
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_tool.allowed",
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                metadata={
                    "mcp_server_id": str(mcp_server_id),
                    "tool_name": allow.tool_name,
                    "risk_level": allow.risk_level,
                },
            )
        commit_or_raise_conflict(self._session, "MCP tool is already allowed for this server")
        self._session.refresh(allow)
        return allow

    def disable_mcp_server(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpServer:
        server = self._require_server(workspace_id, mcp_server_id)
        server.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.disabled",
                target_type="mcp_server",
                target_id=server.id,
                metadata={"name": server.name},
            )
        self._session.commit()
        self._session.refresh(server)
        return server

    def record_mcp_server_health_check(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        actor_user_id: UUID | None,
        data: McpServerHealthCheckRequest,
    ) -> McpServer:
        server = self._require_server(workspace_id, mcp_server_id)
        previous = {
            "health_status": server.health_status,
            "last_health_check_at": server.last_health_check_at.isoformat()
            if server.last_health_check_at is not None
            else None,
            "last_error_configured": server.last_error is not None,
        }
        server.health_status = data.health_status
        server.last_health_check_at = datetime.now(UTC)
        server.last_error = _mcp_health_error(data.health_status, data.error_code)
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.health_check_recorded",
                target_type="mcp_server",
                target_id=server.id,
                metadata={
                    "name": server.name,
                    "previous": previous,
                    "health_status": server.health_status,
                    "error_code": server.last_error,
                    "last_error_configured": server.last_error is not None,
                },
            )
        self._session.commit()
        self._session.refresh(server)
        return server

    def disable_mcp_tool(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        allowlist_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpToolAllowlist:
        self._require_server(workspace_id, mcp_server_id)
        allow = self._session.get(McpToolAllowlist, allowlist_id)
        if (
            allow is None
            or allow.workspace_id != workspace_id
            or allow.mcp_server_id != mcp_server_id
        ):
            raise ValueError("MCP tool allowlist entry not found")
        allow.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_tool.disabled",
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                metadata={
                    "mcp_server_id": str(mcp_server_id),
                    "tool_name": allow.tool_name,
                },
            )
        self._session.commit()
        self._session.refresh(allow)
        return allow

    def list_allowed_mcp_tools(
        self,
        workspace_id: UUID,
    ) -> list[tuple[McpToolAllowlist, McpServer]]:
        statement = (
            select(McpToolAllowlist, McpServer)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.status == "active",
                McpServer.status == "active",
            )
            .order_by(McpServer.name.asc(), McpToolAllowlist.tool_name.asc())
        )
        return [(row[0], row[1]) for row in self._session.execute(statement).all()]

    def list_mcp_catalog(
        self,
        workspace_id: UUID,
        page: PageParams,
        agent_profile_id: UUID | None = None,
    ) -> tuple[list[McpCatalogServer], int]:
        allowed_names: set[str] | None = None
        if agent_profile_id is not None:
            agent = self._session.get(AgentProfile, agent_profile_id)
            if agent is None or agent.workspace_id != workspace_id:
                raise ValueError("Agent profile not found")
            allowed_names = self._agent_allowed_mcp_tool_names(agent)

        servers, total = self.list_mcp_servers(workspace_id, page)
        server_ids = [server.id for server in servers]
        if not server_ids:
            return [], total

        tool_statement = (
            select(McpToolAllowlist)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.mcp_server_id.in_(server_ids),
                McpToolAllowlist.status == "active",
            )
            .order_by(McpToolAllowlist.tool_name.asc())
        )
        if allowed_names is not None:
            if not allowed_names:
                tool_rows: list[McpToolAllowlist] = []
            else:
                tool_rows = list(
                    self._session.scalars(
                        tool_statement.where(McpToolAllowlist.tool_name.in_(allowed_names))
                    )
                )
        else:
            tool_rows = list(self._session.scalars(tool_statement))

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
        usage_by_server_tool = self._mcp_usage_by_server_tool(workspace_id, server_ids)
        hourly_limit_counts = self._mcp_call_limit_counts(
            workspace_id,
            server_ids,
            since=datetime.now(UTC) - timedelta(hours=1),
        )

        tools_by_server: dict[UUID, list[McpCatalogTool]] = {
            server_id: [] for server_id in server_ids
        }
        for allow in tool_rows:
            hourly_count = hourly_limit_counts.get((allow.mcp_server_id, allow.tool_name), 0)
            tools_by_server.setdefault(allow.mcp_server_id, []).append(
                McpCatalogTool(
                    allow,
                    usage_by_server_tool.get(
                        (allow.mcp_server_id, allow.tool_name),
                        _empty_mcp_usage(),
                    ),
                    _mcp_tool_policy_summary(allow.policy, hourly_count=hourly_count),
                )
            )

        credential_counts: dict[UUID, int] = {server_id: 0 for server_id in server_ids}
        workspace_credential_count = 0
        for credential in credentials:
            if credential.mcp_server_id is None:
                workspace_credential_count += 1
                continue
            if credential.mcp_server_id in credential_counts:
                credential_counts[credential.mcp_server_id] += 1

        return [
            self._catalog_entry(
                server,
                tools_by_server.get(server.id, []),
                credential_counts.get(server.id, 0),
                workspace_credential_count,
                _rollup_mcp_usage(server.id, usage_by_server_tool),
            )
            for server in servers
        ], total

    def mcp_tools_for_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
    ) -> list[tuple[McpToolAllowlist, McpServer]]:
        agent = self._session.get(AgentProfile, agent_profile_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")
        allowed_names = self._agent_allowed_mcp_tool_names(agent)
        tools = self.list_allowed_mcp_tools(workspace_id)
        if allowed_names is None:
            return tools
        return [(allow, server) for allow, server in tools if allow.tool_name in allowed_names]

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

    def _agents_using_skill_install(
        self,
        workspace_id: UUID,
        install_id: UUID,
    ) -> list[dict[str, object]]:
        agents = self._session.scalars(
            select(AgentProfile)
            .where(AgentProfile.workspace_id == workspace_id)
            .order_by(AgentProfile.name.asc(), AgentProfile.id.asc())
        ).all()
        affected: list[dict[str, object]] = []
        for agent in agents:
            if install_id not in _agent_installed_skill_ids(agent):
                continue
            policy_mode, configured_tool_names = self._agent_mcp_policy_mode(agent)
            affected.append(
                {
                    "agent_profile_id": agent.id,
                    "name": agent.name,
                    "role": agent.role,
                    "status": agent.status,
                    "policy_mode": policy_mode,
                    "configured_mcp_tools": configured_tool_names,
                }
            )
        return affected

    def _agent_skill_diagnostic(
        self,
        workspace_id: UUID,
        install: WorkspaceSkillInstall,
    ) -> dict[str, object]:
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
    ) -> dict[str, object]:
        availability = _skill_tool_availability(
            tool_name,
            allowed_tool,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
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

    def create_credential_reference(
        self,
        workspace_id: UUID,
        data: McpCredentialReferenceCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        if data.mcp_server_id is not None:
            self._require_server(workspace_id, data.mcp_server_id)
        credential = McpCredentialReference(
            workspace_id=workspace_id,
            **data.model_dump(exclude={"secret_payload"}),
        )
        if data.secret_payload is not None:
            if self._secret_service is None:
                raise ValueError("Hosted credential encryption is not configured")
            encrypted = self._secret_service.encrypt_payload(data.secret_payload)
            credential.provider = "hosted"
            credential.external_ref = ""
            credential.encrypted_secret_payload = encrypted.ciphertext
            credential.secret_fingerprint = encrypted.fingerprint
            credential.encryption_key_id = encrypted.key_id
        self._session.add(credential)
        flush_or_raise_conflict(self._session, "MCP credential name already exists")
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_credential.created",
                target_type="mcp_credential_reference",
                target_id=credential.id,
                metadata={
                    "name": credential.name,
                    "mcp_server_id": str(credential.mcp_server_id)
                    if credential.mcp_server_id is not None
                    else None,
                    "provider": credential.provider,
                    "has_hosted_secret": credential.encrypted_secret_payload is not None,
                },
            )
        commit_or_raise_conflict(self._session, "MCP credential name already exists")
        self._session.refresh(credential)
        return credential

    def list_credential_references(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        mcp_server_id: UUID | None = None,
        include_disabled: bool = False,
    ) -> tuple[list[McpCredentialReference], int]:
        if mcp_server_id is not None:
            self._require_server(workspace_id, mcp_server_id)
        statement = select(McpCredentialReference).where(
            McpCredentialReference.workspace_id == workspace_id
        )
        if mcp_server_id is not None:
            statement = statement.where(McpCredentialReference.mcp_server_id == mcp_server_id)
        if not include_disabled:
            statement = statement.where(McpCredentialReference.status == "active")
        return self._page(
            statement.order_by(
                McpCredentialReference.status.asc(),
                McpCredentialReference.created_at.desc(),
            ),
            page,
        )

    def disable_credential_reference(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        credential = self._session.get(McpCredentialReference, credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            raise ValueError("MCP credential reference not found")
        credential.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_credential.disabled",
                target_type="mcp_credential_reference",
                target_id=credential.id,
                metadata={
                    "name": credential.name,
                    "mcp_server_id": str(credential.mcp_server_id)
                    if credential.mcp_server_id is not None
                    else None,
                },
            )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def log_mcp_tool_call(
        self,
        workspace_id: UUID,
        data: McpToolCallLogRequest,
    ) -> McpToolCallLog:
        if data.mcp_server_id is not None:
            self._require_server(workspace_id, data.mcp_server_id)
        allow = self._allowed_tool_by_name(workspace_id, data.tool_name)
        if allow is None:
            raise ValueError("MCP tool is not allowed for this workspace")
        if data.mcp_server_id is not None and allow.mcp_server_id != data.mcp_server_id:
            raise ValueError("MCP tool is not allowed for this server")
        run = self._mcp_log_run_context(workspace_id, data)
        agent = (
            self._session.get(AgentProfile, run.agent_profile_id)
            if run is not None and run.agent_profile_id is not None
            else None
        )
        agent_allowed_tools = (
            self._agent_allowed_mcp_tool_names(agent) if agent is not None else None
        )
        if agent_allowed_tools is not None and data.tool_name not in agent_allowed_tools:
            raise ValueError("MCP tool is not allowed for this agent")
        request_payload = data.request
        response_payload = data.response
        error_payload = data.error
        payload = data.model_dump()
        if run is not None:
            payload.update(
                {
                    "task_id": run.task_id,
                    "task_step_id": run.task_step_id,
                    "agent_profile_id": run.agent_profile_id,
                }
            )
        log = McpToolCallLog(
            workspace_id=workspace_id,
            latency_ms=_latency_ms_from_payload(response_payload, error_payload),
            argument_sha256=_hash_from_payload(request_payload, "arguments_sha256"),
            response_sha256=_response_hash_from_payload(response_payload),
            error_code=_error_code_from_payload(error_payload),
            created_at=datetime.now(UTC),
            **payload,
        )
        self._session.add(log)
        self._session.commit()
        self._session.refresh(log)
        return log

    def list_mcp_tool_call_logs(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        mcp_server_id: UUID | None = None,
        tool_name: str | None = None,
        status: str | None = None,
    ) -> tuple[list[McpToolCallLog], int]:
        if mcp_server_id is not None:
            self._require_server(workspace_id, mcp_server_id)
        statement = select(McpToolCallLog).where(McpToolCallLog.workspace_id == workspace_id)
        if mcp_server_id is not None:
            statement = statement.where(McpToolCallLog.mcp_server_id == mcp_server_id)
        if tool_name is not None:
            statement = statement.where(McpToolCallLog.tool_name == tool_name)
        if status is not None:
            statement = statement.where(McpToolCallLog.status == status)
        return self._page(statement.order_by(McpToolCallLog.created_at.desc()), page)

    def _can_use_skill(self, workspace_id: UUID, skill: Skill) -> bool:
        return skill.visibility == "public" or skill.owner_workspace_id == workspace_id

    def _mcp_log_run_context(
        self,
        workspace_id: UUID,
        data: McpToolCallLogRequest,
    ) -> AgentRun | None:
        if data.agent_run_id is None:
            return None
        run = self._session.get(AgentRun, data.agent_run_id)
        if run is None or run.workspace_id != workspace_id:
            raise ValueError("Agent run not found")
        return run

    def _allowed_tool_by_name(self, workspace_id: UUID, tool_name: str) -> McpToolAllowlist | None:
        return self._session.scalar(
            select(McpToolAllowlist)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.tool_name == tool_name,
                McpToolAllowlist.status == "active",
                McpServer.status == "active",
            )
        )

    def _require_server(self, workspace_id: UUID, server_id: UUID) -> McpServer:
        server = self._session.get(McpServer, server_id)
        if server is None or server.workspace_id != workspace_id:
            raise ValueError("MCP server not found")
        return server

    def _agent_allowed_mcp_tool_names(self, agent: AgentProfile) -> set[str] | None:
        configured = agent.tool_policy.get("mcp_tools")
        if configured in (None, "*"):
            return None
        if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
            return set(configured)
        return set()

    def _agent_mcp_policy_mode(self, agent: AgentProfile) -> tuple[str, list[str] | None]:
        configured = agent.tool_policy.get("mcp_tools")
        if configured in (None, "*"):
            return "all_workspace_tools", None
        if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
            return "allowlist", sorted(set(configured))
        return "deny_all", []

    def _catalog_entry(
        self,
        server: McpServer,
        tools: list[McpCatalogTool],
        credential_count: int,
        workspace_credential_count: int,
        usage: McpCatalogUsage,
    ) -> McpCatalogServer:
        credential_status = _credential_status(
            server,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
        )
        blocked_reasons = _mcp_blocked_reasons(
            server,
            tools=tools,
            credential_status=credential_status,
        )
        return McpCatalogServer(
            server=server,
            tools=tools,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
            credential_status=credential_status,
            execution_mode=_execution_mode(server),
            executable=not blocked_reasons,
            blocked_reasons=blocked_reasons,
            connection_summary=_connection_summary(server),
            usage=usage,
        )

    def _mcp_usage_by_server_tool(
        self,
        workspace_id: UUID,
        server_ids: list[UUID],
    ) -> dict[tuple[UUID, str], McpCatalogUsage]:
        logs = self._session.scalars(
            select(McpToolCallLog)
            .where(
                McpToolCallLog.workspace_id == workspace_id,
                McpToolCallLog.mcp_server_id.in_(server_ids),
            )
            .order_by(McpToolCallLog.created_at.asc())
        ).all()
        states: dict[tuple[UUID, str], _McpUsageState] = {}
        for log in logs:
            if log.mcp_server_id is None:
                continue
            key = (log.mcp_server_id, log.tool_name)
            states.setdefault(key, _McpUsageState()).add(log)
        return {key: state.to_usage() for key, state in states.items()}

    def _mcp_call_limit_counts(
        self,
        workspace_id: UUID,
        server_ids: list[UUID],
        *,
        since: datetime,
    ) -> dict[tuple[UUID, str], int]:
        logs = self._session.scalars(
            select(McpToolCallLog).where(
                McpToolCallLog.workspace_id == workspace_id,
                McpToolCallLog.mcp_server_id.in_(server_ids),
                McpToolCallLog.status.in_(MCP_LIMIT_COUNTED_STATUSES),
                McpToolCallLog.created_at >= since,
            )
        ).all()
        counts: Counter[tuple[UUID, str]] = Counter()
        for log in logs:
            if log.mcp_server_id is None:
                continue
            counts[(log.mcp_server_id, log.tool_name)] += 1
        return dict(counts)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _skill_checksum(skill: Skill) -> str:
    payload = {
        "key": skill.key,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "capability_keys": skill.capability_keys,
        "manifest": skill.manifest,
        "owner_workspace_id": str(skill.owner_workspace_id)
        if skill.owner_workspace_id is not None
        else None,
        "visibility": skill.visibility,
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _require_same_skill_key(install: WorkspaceSkillInstall, skill: Skill) -> None:
    if install.installed_key != skill.key:
        raise ValueError("Skill key mismatch")


def _append_skill_install_history(
    config: dict[str, object],
    install: WorkspaceSkillInstall,
    *,
    action: str,
    user_id: UUID,
) -> dict[str, object]:
    next_config = dict(config)
    lifecycle = next_config.get("_lifecycle")
    lifecycle_payload = dict(lifecycle) if isinstance(lifecycle, dict) else {}
    raw_history = lifecycle_payload.get("history")
    history = list(raw_history) if isinstance(raw_history, list) else []
    history.append(
        {
            "skill_id": str(install.skill_id),
            "installed_key": install.installed_key,
            "installed_version": install.installed_version,
            "source_checksum": install.source_checksum,
            "status": install.status,
            "recorded_at": datetime.now(UTC).isoformat(),
            "action": action,
            "user_id": str(user_id),
        }
    )
    lifecycle_payload["history"] = history[-20:]
    next_config["_lifecycle"] = lifecycle_payload
    return next_config


def _config_with_lifecycle(
    config: dict[str, object],
    lifecycle_config: dict[str, object],
) -> dict[str, object]:
    next_config = dict(config)
    lifecycle = lifecycle_config.get("_lifecycle")
    if isinstance(lifecycle, dict):
        next_config["_lifecycle"] = lifecycle
    return next_config


def _latest_history_skill_id(config: dict[str, object]) -> UUID | None:
    lifecycle = config.get("_lifecycle")
    if not isinstance(lifecycle, dict):
        return None
    history = lifecycle.get("history")
    if not isinstance(history, list) or not history:
        return None
    latest = history[-1]
    if not isinstance(latest, dict):
        return None
    raw_skill_id = latest.get("skill_id")
    try:
        return UUID(str(raw_skill_id))
    except (TypeError, ValueError):
        return None


def _hash_from_payload(payload: dict[str, object] | None, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _response_hash_from_payload(payload: dict[str, object] | None) -> str | None:
    existing = _hash_from_payload(payload, "response_sha256")
    if existing is not None:
        return existing
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        normalized = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(normalized.encode("utf-8")).hexdigest()
    return None


def _latency_ms_from_payload(
    response: dict[str, object] | None,
    error: dict[str, object] | None,
) -> int | None:
    for payload in (response, error):
        if not isinstance(payload, dict):
            continue
        value = payload.get("latency_ms")
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _error_code_from_payload(payload: dict[str, object] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    return code if isinstance(code, str) else None


@dataclass
class _McpUsageState:
    call_count: int = 0
    failed_call_count: int = 0
    last_call_at: datetime | None = None
    last_call_status: str | None = None
    last_error_code: str | None = None

    def add(self, log: McpToolCallLog) -> None:
        self.call_count += 1
        if _mcp_log_failed(log):
            self.failed_call_count += 1
        if self.last_call_at is None or log.created_at >= self.last_call_at:
            self.last_call_at = log.created_at
            self.last_call_status = log.status
            self.last_error_code = log.error_code

    def merge(self, usage: McpCatalogUsage) -> None:
        self.call_count += usage.call_count
        self.failed_call_count += usage.failed_call_count
        if usage.last_call_at is None:
            return
        if self.last_call_at is None or usage.last_call_at >= self.last_call_at:
            self.last_call_at = usage.last_call_at
            self.last_call_status = usage.last_call_status
            self.last_error_code = usage.last_error_code

    def to_usage(self) -> McpCatalogUsage:
        return McpCatalogUsage(
            call_count=self.call_count,
            failed_call_count=self.failed_call_count,
            last_call_at=self.last_call_at,
            last_call_status=self.last_call_status,
            last_error_code=self.last_error_code,
        )


def _empty_mcp_usage() -> McpCatalogUsage:
    return McpCatalogUsage(
        call_count=0,
        failed_call_count=0,
        last_call_at=None,
        last_call_status=None,
        last_error_code=None,
    )


def _rollup_mcp_usage(
    server_id: UUID,
    usage_by_server_tool: dict[tuple[UUID, str], McpCatalogUsage],
) -> McpCatalogUsage:
    state = _McpUsageState()
    for (candidate_server_id, _tool_name), usage in usage_by_server_tool.items():
        if candidate_server_id == server_id:
            state.merge(usage)
    return state.to_usage()


def _mcp_log_failed(log: McpToolCallLog) -> bool:
    return log.error_code is not None or log.status in {
        "blocked",
        "failed",
        "error",
        "timeout",
    }


def _mcp_tool_policy_summary(
    policy: dict[str, object],
    *,
    hourly_count: int,
) -> dict[str, object]:
    max_calls_per_hour = _optional_positive_int(policy, "max_calls_per_hour")
    return {
        "timeout_seconds": _positive_int(policy, "timeout_seconds", 30),
        "max_input_bytes": _positive_int(policy, "max_input_bytes", 64_000),
        "max_output_bytes": _positive_int(policy, "max_output_bytes", 256_000),
        "max_calls_per_run": _optional_positive_int(policy, "max_calls_per_run"),
        "max_calls_per_hour": max_calls_per_hour,
        "current_hour_call_count": hourly_count,
        "hourly_limit_remaining": (
            max(max_calls_per_hour - hourly_count, 0)
            if max_calls_per_hour is not None
            else None
        ),
        "limit_window_seconds": 3600,
    }


def _positive_int(policy: dict[str, object], key: str, default: int) -> int:
    value = policy.get(key)
    return value if isinstance(value, int) and value > 0 else default


def _optional_positive_int(policy: dict[str, object], key: str) -> int | None:
    value = policy.get(key)
    return value if isinstance(value, int) and value > 0 else None


def _manifest_mcp_tools(manifest: dict[str, object]) -> list[str]:
    raw_tools = manifest.get("mcp_tools")
    if raw_tools is None:
        raw_tools = manifest.get("tools")
    if not isinstance(raw_tools, list):
        return []
    tools: list[str] = []
    seen: set[str] = set()
    for item in raw_tools:
        tool_name: str | None = None
        if isinstance(item, str):
            tool_name = item
        elif isinstance(item, dict) and isinstance(item.get("tool_name"), str):
            tool_name = item["tool_name"]
        if tool_name is None or not tool_name.strip() or tool_name in seen:
            continue
        tools.append(tool_name)
        seen.add(tool_name)
    return tools


def _agent_installed_skill_ids(agent: AgentProfile) -> list[UUID]:
    raw_ids = agent.skills.get("installed_skill_ids")
    if raw_ids is None:
        raw_ids = agent.skills.get("workspace_skill_install_ids")
    if not isinstance(raw_ids, list):
        return []
    install_ids: list[UUID] = []
    seen: set[UUID] = set()
    for raw_id in raw_ids:
        try:
            install_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        if install_id in seen:
            continue
        install_ids.append(install_id)
        seen.add(install_id)
    return install_ids


def _skill_tool_availability(
    tool_name: str,
    allowed_tool: tuple[McpToolAllowlist, McpServer] | None,
    *,
    credential_count: int,
    workspace_credential_count: int,
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
    if server.health_status == "checking":
        blocked_reasons.append("health_check_pending")
    if _health_check_stale(server):
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


def _credential_status(
    server: McpServer,
    *,
    credential_count: int,
    workspace_credential_count: int,
) -> str:
    if credential_count > 0:
        return "server_configured"
    if workspace_credential_count > 0:
        return "workspace_configured"
    if _requires_credentials(server):
        return "missing_required"
    return "not_required"


def _requires_credentials(server: McpServer) -> bool:
    configured = server.connection.get("requires_credentials")
    if isinstance(configured, bool):
        return configured
    return server.server_type.lower().strip() == "hosted"


def _execution_mode(server: McpServer) -> str:
    server_type = server.server_type.lower().strip()
    if server_type == "stdio":
        runtime = server.connection.get("runtime")
        if runtime == "self_hosted":
            return "self_hosted_stdio"
        return "isolated_runtime_stdio"
    if server_type in {"http", "https", "http_jsonrpc", "jsonrpc"}:
        return "remote_http"
    if server_type in {"sse", "http_sse"}:
        return "remote_sse"
    if server_type == "hosted":
        return "hosted"
    return "unsupported"


def _mcp_blocked_reasons(
    server: McpServer,
    *,
    tools: list[McpCatalogTool],
    credential_status: str,
) -> list[str]:
    reasons: list[str] = []
    if server.status != "active":
        reasons.append("server_inactive")
    if server.health_status == "unhealthy":
        reasons.append("server_unhealthy")
    if server.health_status == "checking":
        reasons.append("health_check_pending")
    if _health_check_stale(server):
        reasons.append("health_check_stale")
    if not tools:
        reasons.append("no_allowed_tools")
    if credential_status == "missing_required":
        reasons.append("missing_required_credentials")
    if _execution_mode(server) == "unsupported":
        reasons.append("unsupported_server_type")
    if server.server_type.lower().strip() == "stdio" and not _has_stdio_command(server):
        reasons.append("missing_stdio_command")
    remote_server_types = {"http", "https", "http_jsonrpc", "jsonrpc", "sse", "http_sse"}
    if server.server_type.lower().strip() in remote_server_types and not _has_remote_url(server):
        reasons.append("missing_remote_url")
    if server.server_type.lower().strip() == "hosted":
        transport = str(server.connection.get("transport") or "").lower().strip()
        if transport not in {"http", "https", "http_jsonrpc", "jsonrpc", "sse", "http_sse"}:
            reasons.append("unsupported_hosted_transport")
        if not _has_remote_url(server):
            reasons.append("missing_remote_url")
    return reasons


def _skill_governance_actions(blocked_reasons: list[str]) -> list[str]:
    actions: list[str] = []
    reasons = set(blocked_reasons)
    if "skill_install_disabled" in reasons:
        actions.append("review_skill_install_status")
    if "missing_required_mcp_tools" in reasons:
        actions.append("repair_required_mcp_tools")
    return actions


def _agent_governance_actions(blocked_reasons: list[str]) -> list[str]:
    actions: list[str] = []
    reasons = set(blocked_reasons)
    if "agent_inactive" in reasons:
        actions.append("review_agent_status")
    if "missing_agent_skill_installs" in reasons:
        actions.append("repair_agent_skill_assignments")
    if "configured_mcp_tools_not_allowed" in reasons:
        actions.append("allow_or_remove_configured_mcp_tools")
    if "unusable_installed_skills" in reasons:
        actions.append("repair_installed_skill_dependencies")
    if "unavailable_allowed_mcp_tools" in reasons:
        actions.append("repair_mcp_tool_availability")
    return actions


def _mcp_server_governance_actions(
    blocked_reasons: list[str],
    *,
    failed_call_count: int,
) -> list[str]:
    actions: list[str] = []
    reasons = set(blocked_reasons)
    if "server_inactive" in reasons:
        actions.append("review_mcp_server_status")
    if "server_unhealthy" in reasons or "health_check_stale" in reasons:
        actions.append("refresh_mcp_health_check")
    if "health_check_pending" in reasons:
        actions.append("wait_for_mcp_health_check")
    if "no_allowed_tools" in reasons:
        actions.append("allow_mcp_tools")
    if "missing_required_credentials" in reasons:
        actions.append("add_mcp_credentials")
    if "unsupported_server_type" in reasons:
        actions.append("replace_unsupported_mcp_server")
    if "missing_stdio_command" in reasons:
        actions.append("configure_stdio_command")
    if "missing_remote_url" in reasons or "unsupported_hosted_transport" in reasons:
        actions.append("repair_mcp_connection")
    if failed_call_count > 0:
        actions.append("inspect_failed_mcp_calls")
    return actions


def _governance_actions(actions: list[str] | None) -> list[str]:
    if not actions:
        return list(DEFAULT_GOVERNANCE_APPLY_ACTIONS)
    normalized: list[str] = []
    seen: set[str] = set()
    for action in actions:
        if not isinstance(action, str):
            continue
        item = action.strip()
        if not item or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized or list(DEFAULT_GOVERNANCE_APPLY_ACTIONS)


def _skill_install_should_be_disabled(blocked_reasons: list[str]) -> bool:
    return bool(set(blocked_reasons) & SKILL_INSTALL_GOVERNANCE_DISABLE_REASONS)


def _mcp_server_should_be_governance_disabled(blocked_reasons: list[str]) -> bool:
    return bool(set(blocked_reasons) & MCP_SERVER_GOVERNANCE_DISABLE_REASONS)


def _mcp_server_health_check_should_refresh(blocked_reasons: list[str]) -> bool:
    return bool(set(blocked_reasons) & {"health_check_stale", "server_unhealthy"})


def _governance_result(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    resource_name: str,
    status: str,
    blocked_reasons: list[str],
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "status": status,
        "blocked_reasons": blocked_reasons,
    }


def _governance_skipped(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    resource_name: str,
    reason: str,
    blocked_reasons: list[str],
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "status": "skipped",
        "reason": reason,
        "blocked_reasons": blocked_reasons,
    }


def _health_check_stale(server: McpServer) -> bool:
    checked_at = server.last_health_check_at
    if checked_at is None:
        return False
    normalized = checked_at if checked_at.tzinfo is not None else checked_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - normalized > MCP_HEALTH_CHECK_STALE_AFTER


def _mcp_health_error(health_status: str, error_code: str | None) -> str | None:
    if health_status == "healthy":
        return None
    normalized = error_code.strip() if isinstance(error_code, str) else ""
    if normalized:
        return normalized
    if health_status == "unhealthy":
        return "health_check_failed"
    return None


def _connection_summary(server: McpServer) -> dict[str, object]:
    connection = server.connection
    summary: dict[str, object] = {
        "requires_credentials": _requires_credentials(server),
    }
    transport = connection.get("transport")
    if isinstance(transport, str) and transport:
        summary["transport"] = transport
    url = connection.get("url") or connection.get("endpoint")
    if isinstance(url, str) and url:
        parsed = urlparse(url)
        summary["remote_host"] = parsed.netloc or None
        summary["has_remote_url"] = True
    else:
        summary["has_remote_url"] = False
    summary["has_stdio_command"] = _has_stdio_command(server)
    return summary


def _has_stdio_command(server: McpServer) -> bool:
    command = server.connection.get("command")
    if isinstance(command, str):
        return bool(command.strip())
    if isinstance(command, list):
        return any(isinstance(item, str) and bool(item.strip()) for item in command)
    return False


def _has_remote_url(server: McpServer) -> bool:
    url = server.connection.get("url") or server.connection.get("endpoint")
    return isinstance(url, str) and url.lower().startswith(("https://", "http://"))
