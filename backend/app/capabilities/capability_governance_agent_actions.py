from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.capabilities.capability_governance_rules import (
    governance_result,
    governance_skipped,
    string_list,
)
from backend.app.capabilities.skill_tool_diagnostics import SkillToolDiagnosticsService
from backend.app.core.config import Settings, get_settings
from backend.app.observability.audit_service import AuditService


class CapabilityGovernanceAgentActionService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

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
        diagnostics_service = SkillToolDiagnosticsService(self._session, self._settings)
        for agent in agents:
            diagnostics = diagnostics_service.agent_tool_policy_diagnostics(
                workspace_id,
                agent.id,
            )
            blocked_reasons = string_list(diagnostics.get("blocked_reasons"))
            missing_tools = string_list(diagnostics.get("missing_policy_tools"))
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
            result = governance_result(
                action="allow_or_remove_configured_mcp_tools",
                resource_type="agent_profile",
                resource_id=agent.id,
                resource_name=agent.name,
                status="would_apply" if dry_run else "applied",
                blocked_reasons=blocked_reasons,
            )
            result.update(
                {
                    "removed_mcp_tools": missing_tools,
                    "remaining_mcp_tools": repaired_tools,
                }
            )
            results.append(result)
        return results, skipped
