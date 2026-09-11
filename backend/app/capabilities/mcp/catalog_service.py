from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.capabilities.agent_tool_policy import agent_allowed_mcp_tool_names
from backend.app.capabilities.mcp.catalog import (
    McpCatalogServer,
    McpCatalogTool,
    McpCatalogUsage,
    McpUsageState,
    empty_mcp_usage,
    rollup_mcp_usage,
)
from backend.app.capabilities.mcp.policy import (
    MCP_LIMIT_COUNTED_STATUSES,
    mcp_tool_policy_summary,
)
from backend.app.capabilities.mcp.server_rules import (
    connection_summary,
    credential_status,
    execution_mode,
    mcp_blocked_reasons,
)
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
)
from backend.app.core.config import Settings
from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars

T = TypeVar("T")


class McpCatalogService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def list_mcp_catalog(
        self,
        workspace_id: UUID,
        page: PageParams,
        agent_profile_id: UUID | None = None,
    ) -> tuple[list[McpCatalogServer], int]:
        allowed_names = self._agent_allowed_tool_names(workspace_id, agent_profile_id)
        servers, total = self.list_mcp_servers(workspace_id, page)
        server_ids = [server.id for server in servers]
        if not server_ids:
            return [], total

        tool_rows = self._catalog_tool_rows(
            workspace_id,
            server_ids,
            allowed_names=allowed_names,
        )
        credential_counts, workspace_credential_count = self._credential_counts_for_servers(
            workspace_id,
            server_ids,
        )
        usage_by_server_tool = self._mcp_usage_by_server_tool(workspace_id, server_ids)
        hourly_limit_counts = self._mcp_call_limit_counts(
            workspace_id,
            server_ids,
            since=datetime.now(UTC) - timedelta(hours=1),
        )
        tools_by_server = self._catalog_tools_by_server(
            server_ids,
            tool_rows,
            usage_by_server_tool=usage_by_server_tool,
            hourly_limit_counts=hourly_limit_counts,
        )
        return [
            self._catalog_entry(
                server,
                tools_by_server.get(server.id, []),
                credential_counts.get(server.id, 0),
                workspace_credential_count,
                rollup_mcp_usage(server.id, usage_by_server_tool),
            )
            for server in servers
        ], total

    def mcp_tools_for_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
    ) -> list[tuple[McpToolAllowlist, McpServer]]:
        allowed_names = self._agent_allowed_tool_names(workspace_id, agent_profile_id)
        tools = self.list_allowed_mcp_tools(workspace_id)
        if allowed_names is None:
            return tools
        return [(allow, server) for allow, server in tools if allow.tool_name in allowed_names]

    def list_mcp_servers(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[McpServer], int]:
        statement = (
            select(McpServer)
            .where(McpServer.workspace_id == workspace_id)
            .order_by(McpServer.created_at.desc())
        )
        return self._page(statement, page)

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

    def _agent_allowed_tool_names(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID | None,
    ) -> set[str] | None:
        if agent_profile_id is None:
            return None
        agent = self._session.get(AgentProfile, agent_profile_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")
        return agent_allowed_mcp_tool_names(agent)

    def _catalog_tool_rows(
        self,
        workspace_id: UUID,
        server_ids: list[UUID],
        *,
        allowed_names: set[str] | None,
    ) -> list[McpToolAllowlist]:
        statement = (
            select(McpToolAllowlist)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.mcp_server_id.in_(server_ids),
                McpToolAllowlist.status == "active",
            )
            .order_by(McpToolAllowlist.tool_name.asc())
        )
        if allowed_names is None:
            return list(self._session.scalars(statement))
        if not allowed_names:
            return []
        return list(
            self._session.scalars(statement.where(McpToolAllowlist.tool_name.in_(allowed_names)))
        )

    def _catalog_tools_by_server(
        self,
        server_ids: list[UUID],
        tool_rows: list[McpToolAllowlist],
        *,
        usage_by_server_tool: dict[tuple[UUID, str], McpCatalogUsage],
        hourly_limit_counts: dict[tuple[UUID, str], int],
    ) -> dict[UUID, list[McpCatalogTool]]:
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
                        empty_mcp_usage(),
                    ),
                    mcp_tool_policy_summary(allow.policy, hourly_count=hourly_count),
                )
            )
        return tools_by_server

    def _credential_counts_for_servers(
        self,
        workspace_id: UUID,
        server_ids: list[UUID],
    ) -> tuple[dict[UUID, int], int]:
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

    def _catalog_entry(
        self,
        server: McpServer,
        tools: list[McpCatalogTool],
        credential_count: int,
        workspace_credential_count: int,
        usage: McpCatalogUsage,
    ) -> McpCatalogServer:
        status = credential_status(
            server,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
        )
        blocked_reasons = mcp_blocked_reasons(
            server,
            tools=tools,
            credential_status=status,
            stale_after=self._mcp_health_check_stale_after,
        )
        return McpCatalogServer(
            server=server,
            tools=tools,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
            credential_status=status,
            execution_mode=execution_mode(server),
            executable=not blocked_reasons,
            blocked_reasons=blocked_reasons,
            connection_summary=connection_summary(server),
            usage=usage,
        )

    @property
    def _mcp_health_check_stale_after(self) -> timedelta:
        return timedelta(seconds=self._settings.mcp_health_check_stale_after_seconds)

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
        states: dict[tuple[UUID, str], McpUsageState] = {}
        for log in logs:
            if log.mcp_server_id is None:
                continue
            key = (log.mcp_server_id, log.tool_name)
            states.setdefault(key, McpUsageState()).add(log)
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
        return page_scalars(self._session, statement, page)
