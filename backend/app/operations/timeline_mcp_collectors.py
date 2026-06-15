from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_team_context import TeamRuntimeTimelineContext
from backend.app.operations.timeline_utils import (
    apply_time_filters,
    aware_datetime,
    mcp_governance_message,
)


class TeamRuntimeMcpTimelineCollector:
    def __init__(self, session: Session, context: TeamRuntimeTimelineContext) -> None:
        self._session = session
        self._context = context

    def mcp_governance_events(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[TimelineEvent]:
        server_ids = self._team_mcp_server_ids(workspace_id, team_id)
        if not server_ids:
            return []
        statement = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.target_type == "mcp_server",
            AuditEvent.target_id.in_({str(server_id) for server_id in server_ids}),
            AuditEvent.action.in_(
                {
                    "capability_governance.mcp_health_check_refreshed",
                    "capability_governance.mcp_server_disabled",
                }
            ),
        )
        statement = apply_time_filters(statement, AuditEvent.created_at, filters)
        audit_events = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"mcp_governance:{event.id}",
                source_type="mcp_governance",
                event_type=event.action,
                occurred_at=aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=mcp_governance_message(event),
                metadata={
                    "audit_event_id": str(event.id),
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "user_id": str(event.user_id) if event.user_id is not None else None,
                    "target_type": event.target_type,
                    "target_id": event.target_id,
                    "audit_metadata": dict(event.audit_metadata or {}),
                },
            )
            for event in audit_events
        ]

    def _team_mcp_server_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        configured_tools = self._context.configured_mcp_tools(workspace_id, team_id)
        if not configured_tools:
            return set()
        statement = (
            select(McpToolAllowlist.mcp_server_id)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.status == "active",
                McpServer.workspace_id == workspace_id,
            )
        )
        if "*" not in configured_tools:
            statement = statement.where(McpToolAllowlist.tool_name.in_(configured_tools))
        return set(self._session.scalars(statement).all())
