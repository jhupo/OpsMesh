from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.capabilities.agent_tool_policy import agent_mcp_policy_mode
from backend.app.capabilities.skill_manifest_tools import (
    agent_installed_skill_ids,
    manifest_mcp_tools,
)
from backend.app.capabilities.skill_tool_diagnostics import SkillToolDiagnosticsService
from backend.app.capabilities.workspace_skill_lifecycle_helpers import (
    require_installable_skill,
    require_same_skill_key,
    require_workspace_install,
)
from backend.app.core.config import Settings, get_settings


class WorkspaceSkillImpactService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def workspace_skill_impact(
        self,
        workspace_id: UUID,
        install_id: UUID,
        target_skill_id: UUID | None = None,
    ) -> dict[str, object]:
        install = require_workspace_install(self._session, workspace_id, install_id)
        target_skill = (
            require_installable_skill(self._session, workspace_id, target_skill_id)
            if target_skill_id is not None
            else None
        )
        if target_skill is not None:
            require_same_skill_key(install, target_skill)

        current_required_tools = manifest_mcp_tools(install.installed_manifest)
        target_required_tools = (
            manifest_mcp_tools(target_skill.manifest)
            if target_skill is not None
            else current_required_tools
        )
        target_tool_availability = SkillToolDiagnosticsService(
            self._session,
            self._settings,
        ).skill_tool_availability_for_tools(
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
            if install_id not in agent_installed_skill_ids(agent):
                continue
            policy_mode, configured_tool_names = agent_mcp_policy_mode(agent)
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
