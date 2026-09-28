from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import NotFoundError
from backend.app.domains.capabilities.catalog.models import Capability, ToolGroup
from backend.app.domains.capabilities.marketplace.models import MarketplaceListing
from backend.app.domains.capabilities.mcp.models import McpServer, McpToolAllowlist
from backend.app.domains.capabilities.resources.models import CapabilityResource
from backend.app.domains.capabilities.skills.models import Skill, WorkspaceSkillInstall
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.security_events import (
    SecurityAuditService,
    SecurityRequestContext,
)
from backend.app.observability.audit.service import AuditService


class AdminCapabilityKind(StrEnum):
    CAPABILITY = "capability"
    TOOL = "tool"
    SKILL = "skill"
    SKILL_INSTALL = "skill_install"
    CAPABILITY_RESOURCE = "capability_resource"
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    MARKETPLACE_LISTING = "marketplace_listing"


GovernedResource: TypeAlias = (
    Capability
    | ToolGroup
    | Skill
    | WorkspaceSkillInstall
    | CapabilityResource
    | McpServer
    | McpToolAllowlist
    | MarketplaceListing
)


@dataclass(frozen=True)
class AdminCapabilityState:
    kind: AdminCapabilityKind
    resource_id: UUID
    workspace_id: UUID | None
    status: str
    platform_blocked: bool
    platform_previous_status: str | None


class AdminCapabilityGovernanceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def set_block(
        self,
        kind: AdminCapabilityKind,
        resource_id: UUID,
        *,
        workspace_id: UUID | None,
        blocked: bool,
        reason: str,
        request_context: SecurityRequestContext,
    ) -> AdminCapabilityState:
        if workspace_id is not None:
            self._session.scalar(
                select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
            )
        target = self._target(kind, resource_id, workspace_id)
        if target is None:
            raise NotFoundError("Capability resource not found")
        before = target.status
        if blocked and not target.platform_blocked:
            target.platform_previous_status = target.status
            target.status = "disabled"
            target.platform_blocked = True
        elif not blocked and target.platform_blocked:
            target.status = target.platform_previous_status or "disabled"
            target.platform_previous_status = None
            target.platform_blocked = False

        action = "platform.capability.blocked" if blocked else "platform.capability.released"
        metadata: dict[str, object] = {
            "kind": kind.value,
            "before": before,
            "after": target.status,
            "platform_blocked": target.platform_blocked,
            "reason": reason,
        }
        if workspace_id is None:
            SecurityAuditService(self._session).record_request_event(
                request_context=request_context,
                action=action,
                outcome="allowed",
                severity="warning",
                reason=reason,
                metadata=metadata | {"resource_id": str(resource_id), "actor": "platform_admin"},
            )
        else:
            AuditService(self._session).record_system_action(
                workspace_id=workspace_id,
                action=action,
                target_type=kind.value,
                target_id=resource_id,
                metadata=metadata,
                actor_id="platform_admin",
            )
        self._session.commit()
        return AdminCapabilityState(
            kind=kind,
            resource_id=resource_id,
            workspace_id=workspace_id,
            status=target.status,
            platform_blocked=target.platform_blocked,
            platform_previous_status=target.platform_previous_status,
        )

    def _target(
        self,
        kind: AdminCapabilityKind,
        resource_id: UUID,
        workspace_id: UUID | None,
    ) -> GovernedResource | None:
        if kind == AdminCapabilityKind.CAPABILITY:
            if workspace_id is not None:
                raise ValueError("Global capability must not specify workspace_id")
            return self._session.scalar(
                select(Capability).where(Capability.id == resource_id).with_for_update()
            )
        if kind == AdminCapabilityKind.TOOL:
            if workspace_id is not None:
                raise ValueError("Global tool group must not specify workspace_id")
            return self._session.scalar(
                select(ToolGroup).where(ToolGroup.id == resource_id).with_for_update()
            )
        if kind == AdminCapabilityKind.SKILL:
            scope = (
                Skill.owner_workspace_id.is_(None)
                if workspace_id is None
                else Skill.owner_workspace_id == workspace_id
            )
            return self._session.scalar(
                select(Skill).where(Skill.id == resource_id, scope).with_for_update()
            )
        if workspace_id is None:
            raise ValueError("workspace_id is required for workspace capability resources")
        if kind == AdminCapabilityKind.SKILL_INSTALL:
            return self._session.scalar(
                select(WorkspaceSkillInstall)
                .where(
                    WorkspaceSkillInstall.id == resource_id,
                    WorkspaceSkillInstall.workspace_id == workspace_id,
                )
                .with_for_update()
            )
        if kind == AdminCapabilityKind.CAPABILITY_RESOURCE:
            return self._session.scalar(
                select(CapabilityResource)
                .where(
                    CapabilityResource.id == resource_id,
                    CapabilityResource.workspace_id == workspace_id,
                )
                .with_for_update()
            )
        if kind == AdminCapabilityKind.MCP_SERVER:
            return self._session.scalar(
                select(McpServer)
                .where(McpServer.id == resource_id, McpServer.workspace_id == workspace_id)
                .with_for_update()
            )
        if kind == AdminCapabilityKind.MARKETPLACE_LISTING:
            return self._session.scalar(
                select(MarketplaceListing)
                .where(
                    MarketplaceListing.id == resource_id,
                    MarketplaceListing.workspace_id == workspace_id,
                )
                .with_for_update()
            )
        return self._session.scalar(
            select(McpToolAllowlist)
            .where(
                McpToolAllowlist.id == resource_id,
                McpToolAllowlist.workspace_id == workspace_id,
            )
            .with_for_update()
        )
