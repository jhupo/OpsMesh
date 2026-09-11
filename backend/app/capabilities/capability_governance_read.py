from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.capabilities.capability_governance_rules import (
    agent_governance_actions,
    dict_list,
    mcp_server_governance_actions,
    skill_governance_actions,
    string_list,
)
from backend.app.capabilities.mcp.catalog_service import McpCatalogService
from backend.app.capabilities.models import WorkspaceSkillInstall
from backend.app.capabilities.skill_tool_diagnostics import SkillToolDiagnosticsService
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams


class McpGovernanceSummary(TypedDict):
    catalog_total: int
    blocked_reasons: Counter[str]
    allowed_tool_count: int
    high_risk_tool_count: int
    approval_required_tool_count: int
    failed_tool_call_count: int


class CapabilityGovernanceReadService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def workspace_capability_governance(self, workspace_id: UUID) -> dict[str, object]:
        skill_items, skill_blocked_reasons = self._skill_governance_items(workspace_id)
        agent_items, agent_blocked_reasons = self._agent_governance_items(workspace_id)
        server_items, server_summary = self._mcp_server_governance_items(workspace_id)

        blocked_reason_counts: Counter[str] = Counter()
        blocked_reason_counts.update(skill_blocked_reasons)
        blocked_reason_counts.update(agent_blocked_reasons)
        blocked_reason_counts.update(server_summary["blocked_reasons"])
        recommended_actions = Counter(
            action
            for item in [*skill_items, *agent_items, *server_items]
            for action in string_list(item.get("recommended_actions"))
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
                "agents_with_blocks": sum(1 for item in agent_items if item["blocked_reasons"]),
                "mcp_servers": server_summary["catalog_total"],
                "executable_mcp_servers": sum(1 for item in server_items if item["executable"]),
                "blocked_mcp_servers": sum(1 for item in server_items if item["blocked_reasons"]),
                "allowed_mcp_tools": server_summary["allowed_tool_count"],
                "high_risk_mcp_tools": server_summary["high_risk_tool_count"],
                "tools_requiring_approval": server_summary["approval_required_tool_count"],
                "missing_required_credential_servers": sum(
                    1 for item in server_items if item["credential_status"] == "missing_required"
                ),
                "failed_mcp_tool_calls": server_summary["failed_tool_call_count"],
                "catalog_truncated": server_summary["catalog_total"] > len(server_items),
                "blocked_reason_counts": dict(sorted(blocked_reason_counts.items())),
                "recommended_actions": dict(sorted(recommended_actions.items())),
            },
            "skills": skill_items,
            "agents": agent_items,
            "mcp_servers": server_items,
        }

    def _skill_governance_items(
        self,
        workspace_id: UUID,
    ) -> tuple[list[dict[str, object]], Counter[str]]:
        installs = self._session.scalars(
            select(WorkspaceSkillInstall)
            .where(WorkspaceSkillInstall.workspace_id == workspace_id)
            .order_by(
                WorkspaceSkillInstall.status.asc(),
                WorkspaceSkillInstall.installed_key.asc(),
                WorkspaceSkillInstall.created_at.desc(),
            )
        ).all()
        items: list[dict[str, object]] = []
        blocked_reasons: Counter[str] = Counter()
        for install in installs:
            availability = self._diagnostics().workspace_skill_availability(
                workspace_id,
                install.id,
            )
            blocked_reasons.update(availability.blocked_reasons)
            items.append(
                {
                    "install_id": install.id,
                    "installed_key": install.installed_key,
                    "installed_name": install.installed_name,
                    "installed_version": install.installed_version,
                    "status": install.status,
                    "usable": availability.usable,
                    "required_tools": availability.required_tools,
                    "blocked_reasons": availability.blocked_reasons,
                    "recommended_actions": skill_governance_actions(
                        availability.blocked_reasons
                    ),
                }
            )
        return items, blocked_reasons

    def _agent_governance_items(
        self,
        workspace_id: UUID,
    ) -> tuple[list[dict[str, object]], Counter[str]]:
        agents = self._session.scalars(
            select(AgentProfile)
            .where(AgentProfile.workspace_id == workspace_id)
            .order_by(AgentProfile.status.asc(), AgentProfile.name.asc(), AgentProfile.id.asc())
        ).all()
        items: list[dict[str, object]] = []
        blocked_reasons: Counter[str] = Counter()
        for agent in agents:
            diagnostics = self._diagnostics().agent_tool_policy_diagnostics(
                workspace_id,
                agent.id,
            )
            effective_tools = dict_list(diagnostics.get("effective_tools"))
            unavailable_tool_count = sum(
                1
                for tool in effective_tools
                if tool.get("allowed_by_agent_policy") is True and tool.get("available") is False
            )
            agent_blocked_reasons = string_list(diagnostics.get("blocked_reasons"))
            blocked_reasons.update(agent_blocked_reasons)
            items.append(
                {
                    "agent_profile_id": agent.id,
                    "name": agent.name,
                    "role": agent.role,
                    "status": agent.status,
                    "policy_mode": diagnostics["policy_mode"],
                    "configured_mcp_tools": diagnostics["configured_mcp_tools"],
                    "installed_skill_count": len(dict_list(diagnostics.get("installed_skills"))),
                    "effective_tool_count": len(effective_tools),
                    "unavailable_tool_count": unavailable_tool_count,
                    "blocked_reasons": agent_blocked_reasons,
                    "recommended_actions": agent_governance_actions(agent_blocked_reasons),
                }
            )
        return items, blocked_reasons

    def _mcp_server_governance_items(
        self,
        workspace_id: UUID,
    ) -> tuple[list[dict[str, object]], McpGovernanceSummary]:
        catalog_items, catalog_total = McpCatalogService(
            self._session,
            self._settings,
        ).list_mcp_catalog(
            workspace_id,
            PageParams(limit=10_000, offset=0),
        )
        items: list[dict[str, object]] = []
        blocked_reasons: Counter[str] = Counter()
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
            blocked_reasons.update(item.blocked_reasons)
            allowed_tool_count += tool_count
            high_risk_tool_count += server_high_risk_tools
            approval_required_tool_count += server_approval_tools
            failed_tool_call_count += item.usage.failed_call_count
            items.append(
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
                    "recommended_actions": mcp_server_governance_actions(
                        item.blocked_reasons,
                        failed_call_count=item.usage.failed_call_count,
                    ),
                }
            )
        return items, {
            "catalog_total": catalog_total,
            "blocked_reasons": blocked_reasons,
            "allowed_tool_count": allowed_tool_count,
            "high_risk_tool_count": high_risk_tool_count,
            "approval_required_tool_count": approval_required_tool_count,
            "failed_tool_call_count": failed_tool_call_count,
        }

    def _diagnostics(self) -> SkillToolDiagnosticsService:
        return SkillToolDiagnosticsService(self._session, self._settings)
