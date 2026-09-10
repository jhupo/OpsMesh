from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.capabilities.capability_governance_rules import (
    governance_result,
    governance_skipped,
    mcp_health_refresh_error,
    mcp_server_should_be_governance_disabled,
    mcp_server_should_refresh_health,
)
from backend.app.capabilities.mcp_catalog import McpCatalogServer
from backend.app.capabilities.mcp_catalog_service import McpCatalogService
from backend.app.capabilities.mcp_server_rules import (
    connection_summary as _connection_summary,
)
from backend.app.capabilities.mcp_server_rules import (
    mcp_server_probeable as _mcp_server_probeable,
)
from backend.app.capabilities.models import McpToolAllowlist
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams


class CapabilityGovernanceMcpActionService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

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
        catalog_items, _ = self._catalog_items(workspace_id)
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for item in catalog_items:
            server = item.server
            if server.status != "active":
                continue
            if mcp_server_ids and server.id not in mcp_server_ids:
                continue
            blocked_reasons = item.blocked_reasons
            if not mcp_server_should_be_governance_disabled(blocked_reasons):
                skipped.append(
                    governance_skipped(
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
                    governance_skipped(
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
                governance_result(
                    action="disable_blocked_mcp_servers",
                    resource_type="mcp_server",
                    resource_id=server.id,
                    resource_name=server.name,
                    status="would_apply" if dry_run else "applied",
                    blocked_reasons=blocked_reasons,
                )
            )
        return results, skipped

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
        catalog_items, _ = self._catalog_items(workspace_id)
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for item in catalog_items:
            server = item.server
            if server.status != "active":
                continue
            if mcp_server_ids and server.id not in mcp_server_ids:
                continue
            blocked_reasons = item.blocked_reasons
            if "no_allowed_tools" not in blocked_reasons:
                skipped.append(
                    governance_skipped(
                        action="allow_mcp_tools",
                        resource_type="mcp_server",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="mcp_server_allowed_tools_not_missing",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            disabled_tools = self._disabled_mcp_tools(workspace_id, server.id)
            if not disabled_tools:
                skipped.append(
                    governance_skipped(
                        action="allow_mcp_tools",
                        resource_type="mcp_server",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="no_disabled_mcp_tools_to_enable",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            for allow in disabled_tools:
                if len(results) >= limit:
                    skipped.append(
                        governance_skipped(
                            action="allow_mcp_tools",
                            resource_type="mcp_tool_allowlist",
                            resource_id=allow.id,
                            resource_name=allow.tool_name,
                            reason="max_items_reached",
                            blocked_reasons=blocked_reasons,
                        )
                    )
                    continue
                if not dry_run:
                    previous_status = allow.status
                    allow.status = "active"
                    AuditService(self._session).record_user_action(
                        workspace_id=workspace_id,
                        user_id=actor_user_id,
                        action="capability_governance.mcp_tool_reenabled",
                        target_type="mcp_tool_allowlist",
                        target_id=allow.id,
                        metadata={
                            "mcp_server_id": str(server.id),
                            "server_name": server.name,
                            "tool_name": allow.tool_name,
                            "capability_key": allow.capability_key,
                            "risk_level": allow.risk_level,
                            "previous_status": previous_status,
                            "reason": reason,
                        },
                    )
                result = governance_result(
                    action="allow_mcp_tools",
                    resource_type="mcp_tool_allowlist",
                    resource_id=allow.id,
                    resource_name=allow.tool_name,
                    status="would_apply" if dry_run else "applied",
                    blocked_reasons=blocked_reasons,
                )
                result.update(
                    {
                        "mcp_server_id": server.id,
                        "server_name": server.name,
                        "tool_name": allow.tool_name,
                        "previous_status": "disabled",
                    }
                )
                results.append(result)
        return results, skipped

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
        catalog_items, _ = self._catalog_items(workspace_id)
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for item in catalog_items:
            server = item.server
            if server.status != "active":
                continue
            if mcp_server_ids and server.id not in mcp_server_ids:
                continue
            blocked_reasons = item.blocked_reasons
            if not mcp_server_should_refresh_health(blocked_reasons):
                skipped.append(
                    governance_skipped(
                        action="refresh_mcp_health_check",
                        resource_type="mcp_server",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="mcp_server_health_check_not_needed",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if len(results) >= limit:
                skipped.append(
                    governance_skipped(
                        action="refresh_mcp_health_check",
                        resource_type="mcp_server",
                        resource_id=server.id,
                        resource_name=server.name,
                        reason="max_items_reached",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            previous = {
                "health_status": server.health_status,
                "last_health_check_at": server.last_health_check_at.isoformat()
                if server.last_health_check_at is not None
                else None,
                "last_error_configured": server.last_error is not None,
            }
            probeable = _mcp_server_probeable(server)
            next_health_status = "healthy" if probeable else "unhealthy"
            next_error = None if probeable else mcp_health_refresh_error(blocked_reasons)
            if not dry_run:
                server.health_status = next_health_status
                server.last_health_check_at = datetime.now(UTC)
                server.last_error = next_error
                AuditService(self._session).record_user_action(
                    workspace_id=workspace_id,
                    user_id=actor_user_id,
                    action="capability_governance.mcp_health_check_refreshed",
                    target_type="mcp_server",
                    target_id=server.id,
                    metadata={
                        "name": server.name,
                        "previous": previous,
                        "health_status": next_health_status,
                        "last_error_configured": next_error is not None,
                        "blocked_reasons": blocked_reasons,
                        "connection": _connection_summary(server),
                        "reason": reason,
                    },
                )
            result = governance_result(
                action="refresh_mcp_health_check",
                resource_type="mcp_server",
                resource_id=server.id,
                resource_name=server.name,
                status="would_apply" if dry_run else "applied",
                blocked_reasons=blocked_reasons,
            )
            result.update(
                {
                    "previous": previous,
                    "health_status": next_health_status,
                    "last_error_configured": next_error is not None,
                    "connection": _connection_summary(server),
                }
            )
            results.append(result)
        return results, skipped

    def _disabled_mcp_tools(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
    ) -> list[McpToolAllowlist]:
        return list(
            self._session.scalars(
                select(McpToolAllowlist)
                .where(
                    McpToolAllowlist.workspace_id == workspace_id,
                    McpToolAllowlist.mcp_server_id == mcp_server_id,
                    McpToolAllowlist.status == "disabled",
                )
                .order_by(McpToolAllowlist.tool_name.asc(), McpToolAllowlist.id.asc())
            )
        )

    def _catalog_items(self, workspace_id: UUID) -> tuple[list[McpCatalogServer], int]:
        return McpCatalogService(self._session, self._settings).list_mcp_catalog(
            workspace_id,
            PageParams(limit=10_000, offset=0),
        )
