"""Immutable invocation data between transaction preparation and async Runtime I/O."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from opsmesh.agents.execution.contracts import AgentRuntimeToolResult
from opsmesh.capabilities.mcp.execution.contracts import McpExecutionRequest
from opsmesh.capabilities.mcp.execution.policy import McpExecutionPolicy
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.telemetry.trace_context import TraceContext


class McpOperation(Protocol):
    async def execute(self, io: BlockingIO) -> dict[str, object]: ...


@dataclass(frozen=True)
class PreparedMcpExecution:
    request: McpExecutionRequest
    server_id: UUID
    log_id: UUID
    snapshot: dict[str, object]
    policy: McpExecutionPolicy
    operation: McpOperation
    started: float
    trace: TraceContext | None


@dataclass(frozen=True)
class PreparedMcpTool:
    execution: PreparedMcpExecution
    metadata: dict[str, object]
    pending_invocation_id: UUID | None = None


ToolPreparation = AgentRuntimeToolResult | PreparedMcpTool
