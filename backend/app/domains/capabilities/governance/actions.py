from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.capabilities.governance.mcp_actions import (
    CapabilityGovernanceMcpActionService,
)
from backend.app.domains.capabilities.governance.rules import (
    governance_result,
    governance_skipped,
    skill_install_should_be_disabled,
    string_list,
)
from backend.app.domains.capabilities.skills.diagnostics import SkillToolDiagnosticsService
from backend.app.domains.capabilities.skills.models import WorkspaceSkillInstall
from backend.app.observability.audit.service import AuditService


class CapabilityGovernanceActionService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def disable_unusable_skill_installs(
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
        diagnostics = SkillToolDiagnosticsService(self._session, self._settings)
        for install in installs:
            availability = diagnostics.workspace_skill_availability(workspace_id, install.id)
            blocked_reasons = availability.blocked_reasons
            if not skill_install_should_be_disabled(blocked_reasons):
                skipped.append(
                    governance_skipped(
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
                    governance_skipped(
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
                governance_result(
                    action="disable_unusable_skill_installs",
                    resource_type="workspace_skill_install",
                    resource_id=install.id,
                    resource_name=install.installed_key,
                    status="would_apply" if dry_run else "applied",
                    blocked_reasons=blocked_reasons,
                )
            )
        return results, skipped

    def disable_blocked_mcp_servers(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        mcp_server_ids: set[UUID],
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        return self._mcp_actions().disable_blocked_mcp_servers(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
            mcp_server_ids=mcp_server_ids,
            limit=limit,
            reason=reason,
        )

    def allow_mcp_tools(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        mcp_server_ids: set[UUID],
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        return self._mcp_actions().allow_mcp_tools(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
            mcp_server_ids=mcp_server_ids,
            limit=limit,
            reason=reason,
        )

    def refresh_mcp_health_checks(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        mcp_server_ids: set[UUID],
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        return self._mcp_actions().refresh_mcp_health_checks(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
            mcp_server_ids=mcp_server_ids,
            limit=limit,
            reason=reason,
        )

    def remove_unallowed_configured_mcp_tools(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        agents = self._session.scalars(
            select(AgentProfile)
            .where(AgentProfile.workspace_id == workspace_id)
            .order_by(AgentProfile.name.asc(), AgentProfile.id.asc())
        ).all()
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        diagnostics = SkillToolDiagnosticsService(self._session, self._settings)
        for agent in agents:
            result = diagnostics.agent_tool_policy_diagnostics(workspace_id, agent.id)
            blocked_reasons = string_list(result.get("blocked_reasons"))
            missing_tools = string_list(result.get("missing_policy_tools"))
            if not missing_tools:
                skipped.append(
                    governance_skipped(
                        action="allow_or_remove_configured_mcp_tools",
                        resource_type="agent_profile",
                        resource_id=agent.id,
                        resource_name=agent.name,
                        reason="agent_mcp_policy_already_allowed",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if len(results) >= limit:
                skipped.append(
                    governance_skipped(
                        action="allow_or_remove_configured_mcp_tools",
                        resource_type="agent_profile",
                        resource_id=agent.id,
                        resource_name=agent.name,
                        reason="max_items_reached",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            current_tools = string_list(agent.tool_policy.get("mcp_tools"))
            repaired_tools = [tool for tool in current_tools if tool not in set(missing_tools)]
            if not dry_run:
                next_policy = dict(agent.tool_policy)
                next_policy["mcp_tools"] = repaired_tools
                agent.tool_policy = next_policy
                AuditService(self._session).record_user_action(
                    workspace_id=workspace_id,
                    user_id=actor_user_id,
                    action="capability_governance.agent_mcp_policy_repaired",
                    target_type="agent_profile",
                    target_id=agent.id,
                    metadata={
                        "agent_name": agent.name,
                        "removed_mcp_tools": missing_tools,
                        "remaining_mcp_tools": repaired_tools,
                        "blocked_reasons": blocked_reasons,
                        "reason": reason,
                    },
                )
            applied = governance_result(
                action="allow_or_remove_configured_mcp_tools",
                resource_type="agent_profile",
                resource_id=agent.id,
                resource_name=agent.name,
                status="would_apply" if dry_run else "applied",
                blocked_reasons=blocked_reasons,
            )
            applied.update(
                {
                    "removed_mcp_tools": missing_tools,
                    "remaining_mcp_tools": repaired_tools,
                }
            )
            results.append(applied)
        return results, skipped

    def _mcp_actions(self) -> CapabilityGovernanceMcpActionService:
        return CapabilityGovernanceMcpActionService(self._session, self._settings)
