from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.capabilities.capability_governance_actions import (
    CapabilityGovernanceActionService,
)
from backend.app.capabilities.capability_governance_read import CapabilityGovernanceReadService
from backend.app.capabilities.capability_governance_rules import (
    GOVERNANCE_APPLY_ACTIONS,
    governance_actions,
)
from backend.app.core.config import Settings, get_settings


class CapabilityGovernanceService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def workspace_capability_governance(self, workspace_id: UUID) -> dict[str, object]:
        return CapabilityGovernanceReadService(
            self._session,
            self._settings,
        ).workspace_capability_governance(workspace_id)

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
        requested_actions = governance_actions(actions)
        unsupported_actions = sorted(set(requested_actions) - GOVERNANCE_APPLY_ACTIONS)
        if unsupported_actions:
            raise ValueError(f"Unsupported governance action: {unsupported_actions[0]}")

        remaining = max_items
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        if "disable_unusable_skill_installs" in requested_actions and remaining > 0:
            action_results, action_skipped = self._actions().disable_unusable_skill_installs(
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
            action_results, action_skipped = self._actions().disable_blocked_mcp_servers(
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
        if {
            "refresh_mcp_health_check",
            "refresh_mcp_health_checks",
        } & set(requested_actions) and remaining > 0:
            action_results, action_skipped = self._actions().refresh_mcp_health_checks(
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
        if "allow_mcp_tools" in requested_actions and remaining > 0:
            action_results, action_skipped = self._actions().allow_mcp_tools(
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
        if "allow_or_remove_configured_mcp_tools" in requested_actions and remaining > 0:
            action_results, action_skipped = self._actions().remove_unallowed_configured_mcp_tools(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                dry_run=dry_run,
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
            "refreshed_mcp_health_check_count": sum(
                1 for item in results if item["action"] == "refresh_mcp_health_check"
            ),
            "repaired_agent_mcp_policy_count": sum(
                1 for item in results if item["action"] == "allow_or_remove_configured_mcp_tools"
            ),
            "reenabled_mcp_tool_count": sum(
                1 for item in results if item["action"] == "allow_mcp_tools"
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

    def _actions(self) -> CapabilityGovernanceActionService:
        return CapabilityGovernanceActionService(self._session, self._settings)
