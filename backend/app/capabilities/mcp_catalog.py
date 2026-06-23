from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.app.capabilities.models import McpServer, McpToolAllowlist, McpToolCallLog


@dataclass(frozen=True)
class McpCatalogUsage:
    call_count: int
    failed_call_count: int
    last_call_at: datetime | None
    last_call_status: str | None
    last_error_code: str | None


@dataclass(frozen=True)
class McpCatalogTool:
    allowlist: McpToolAllowlist
    usage: McpCatalogUsage
    policy_summary: dict[str, object]


@dataclass(frozen=True)
class McpCatalogServer:
    server: McpServer
    tools: list[McpCatalogTool]
    credential_count: int
    workspace_credential_count: int
    credential_status: str
    execution_mode: str
    executable: bool
    blocked_reasons: list[str]
    connection_summary: dict[str, object]
    usage: McpCatalogUsage


class McpUsageState:
    def __init__(self) -> None:
        self.call_count = 0
        self.failed_call_count = 0
        self.last_call_at: datetime | None = None
        self.last_call_status: str | None = None
        self.last_error_code: str | None = None

    def add(self, log: McpToolCallLog) -> None:
        self.call_count += 1
        if mcp_log_failed(log):
            self.failed_call_count += 1
        if self.last_call_at is None or log.created_at >= self.last_call_at:
            self.last_call_at = log.created_at
            self.last_call_status = log.status
            self.last_error_code = log.error_code

    def merge(self, usage: McpCatalogUsage) -> None:
        self.call_count += usage.call_count
        self.failed_call_count += usage.failed_call_count
        if usage.last_call_at is None:
            return
        if self.last_call_at is None or usage.last_call_at >= self.last_call_at:
            self.last_call_at = usage.last_call_at
            self.last_call_status = usage.last_call_status
            self.last_error_code = usage.last_error_code

    def to_usage(self) -> McpCatalogUsage:
        return McpCatalogUsage(
            call_count=self.call_count,
            failed_call_count=self.failed_call_count,
            last_call_at=self.last_call_at,
            last_call_status=self.last_call_status,
            last_error_code=self.last_error_code,
        )


def empty_mcp_usage() -> McpCatalogUsage:
    return McpCatalogUsage(
        call_count=0,
        failed_call_count=0,
        last_call_at=None,
        last_call_status=None,
        last_error_code=None,
    )


def rollup_mcp_usage(
    server_id: UUID,
    usage_by_server_tool: dict[tuple[UUID, str], McpCatalogUsage],
) -> McpCatalogUsage:
    state = McpUsageState()
    for (candidate_server_id, _tool_name), usage in usage_by_server_tool.items():
        if candidate_server_id == server_id:
            state.merge(usage)
    return state.to_usage()


def mcp_log_failed(log: McpToolCallLog) -> bool:
    return log.error_code is not None or log.status in {
        "blocked",
        "failed",
        "error",
        "timeout",
    }
