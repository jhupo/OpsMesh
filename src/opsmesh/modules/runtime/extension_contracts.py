"""Python-only deployed extensions consumed by SDK adapters through neutral values."""

from collections.abc import Awaitable, Callable
from typing import Any

from opsmesh.framework.agents import (
    AgentExecutionContext,
    ComputerSession,
    FilePatchOperation,
    FilePatchResult,
    ShellExecutionRequest,
    ShellExecutionResult,
)
from opsmesh.framework.errors import ErrorInfo
from opsmesh.framework.typing import OpsMeshContract


class ToolExecutionResult(OpsMeshContract):
    name: str
    output: Any = None
    interrupted: bool = False


class ToolCompletionDecision(OpsMeshContract):
    is_final: bool
    output: Any = None


class AgentToolInput(OpsMeshContract):
    arguments: dict[str, Any]
    schema_summary: str | None = None
    json_schema: dict[str, Any] | None = None


class HostedMCPApprovalRequest(OpsMeshContract):
    server_label: str
    tool_name: str
    arguments: dict[str, Any]
    request_id: str


class ApprovalPolicyDecision(OpsMeshContract):
    approved: bool
    reason: str | None = None


ToolCompletion = Callable[
    [AgentExecutionContext, tuple[ToolExecutionResult, ...]],
    ToolCompletionDecision | Awaitable[ToolCompletionDecision],
]
AgentInputBuilder = Callable[
    [AgentExecutionContext, AgentToolInput],
    str | list[dict[str, Any]] | Awaitable[str | list[dict[str, Any]]],
]
HostedMCPApprovalPolicy = Callable[
    [AgentExecutionContext, HostedMCPApprovalRequest],
    ApprovalPolicyDecision | Awaitable[ApprovalPolicyDecision],
]
ShellExecutor = Callable[
    [AgentExecutionContext, ShellExecutionRequest],
    ShellExecutionResult | Awaitable[ShellExecutionResult],
]
PatchEditor = Callable[
    [AgentExecutionContext, FilePatchOperation], FilePatchResult | Awaitable[FilePatchResult]
]
ComputerFactory = Callable[[AgentExecutionContext], ComputerSession | Awaitable[ComputerSession]]
ComputerSafetyPolicy = Callable[[AgentExecutionContext, dict[str, Any]], bool | Awaitable[bool]]


class MCPToolFailure(OpsMeshContract):
    server_ref: str
    tool_name: str
    call_id: str
    error: ErrorInfo


MCPFailureFormatter = Callable[[AgentExecutionContext, MCPToolFailure], str | Awaitable[str]]
