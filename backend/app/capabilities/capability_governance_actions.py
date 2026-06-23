from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.capabilities.capability_governance_agent_actions import (
    CapabilityGovernanceAgentActionService,
)
from backend.app.capabilities.capability_governance_mcp_actions import (
    CapabilityGovernanceMcpActionService,
)
from backend.app.capabilities.capability_governance_skill_actions import (
    CapabilityGovernanceSkillActionService,
)
from backend.app.core.config import Settings, get_settings


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
        return self._skill_actions().disable_unusable_skill_installs(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
            install_ids=install_ids,
            limit=limit,
            reason=reason,
        )

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
        return self._agent_actions().remove_unallowed_configured_mcp_tools(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
            limit=limit,
            reason=reason,
        )

    def _skill_actions(self) -> CapabilityGovernanceSkillActionService:
        return CapabilityGovernanceSkillActionService(self._session, self._settings)

    def _mcp_actions(self) -> CapabilityGovernanceMcpActionService:
        return CapabilityGovernanceMcpActionService(self._session, self._settings)

    def _agent_actions(self) -> CapabilityGovernanceAgentActionService:
        return CapabilityGovernanceAgentActionService(self._session, self._settings)
