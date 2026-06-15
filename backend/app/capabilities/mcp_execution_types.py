from dataclasses import dataclass
from uuid import UUID


class McpExecutionError(Exception):
    def __init__(self, message: str, *, code: str = "mcp_execution_failed") -> None:
        super().__init__(message)
        self.code = code


class McpExecutionPending(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        response: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.response = response


@dataclass(frozen=True)
class McpExecutionRequest:
    workspace_id: UUID
    agent_run_id: UUID
    tool_name: str
    arguments: dict[str, object]
    mcp_server_id: UUID | None = None
    runtime_allowed_tools: tuple[str, ...] | None = None


@dataclass(frozen=True)
class McpExecutionResult:
    status: str
    response: dict[str, object] | None
    error: dict[str, object] | None
    log_id: UUID
    latency_ms: int
