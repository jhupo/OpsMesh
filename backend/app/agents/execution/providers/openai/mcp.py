"""Public SDK MCPServer extension for governed calls into an OpsMesh Runtime.

SDK transports cannot open an OpsMesh Runtime RPC connection. This extension supplies that
connection; the SDK owns tool conversion, approvals, output conversion and its execution loop.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from agents import Agent, FunctionTool, RunContextWrapper
from agents import Tool as AgentTool
from agents.agent import AgentBase
from agents.mcp import MCPServer
from agents.mcp.util import MCPToolMetaContext
from agents.sandbox import SandboxAgent
from agents.tool_context import ToolContext
from mcp.types import CallToolResult, GetPromptResult, ListPromptsResult, Tool

from backend.app.agents.execution.cancellation import raise_if_cancelled
from backend.app.agents.execution.contracts import AgentRunRequest


class RuntimeMCPServer(MCPServer):
    def __init__(self, request: AgentRunRequest) -> None:
        self.request = request
        self.definitions = {
            tool.name: tool for tool in request.context.tool_definitions if tool.source == "mcp"
        }
        self.approval_reviews: dict[str, dict[str, object]] = {}
        super().__init__(
            require_approval="never",
            tool_meta_resolver=self.invocation_meta,
            failure_error_function=None,
        )

    @property
    def name(self) -> str:
        return "opsmesh-runtime"

    async def connect(self) -> None:
        # Runtime allocation and connection policy were checked before the SDK run.
        await raise_if_cancelled(self.request.cancellation)

    async def cleanup(self) -> None:
        # OpsMesh owns Runtime leases and cleanup, not SDK transport teardown.
        pass

    async def list_tools(
        self, run_context: RunContextWrapper[Any] | None = None, agent: AgentBase[Any] | None = None
    ) -> list[Tool]:
        return [
            Tool(
                name=tool.name,
                title=tool.title or None,
                description=tool.description,
                inputSchema=tool.input_schema,
                outputSchema=tool.output_schema or None,
            )
            for tool in self.definitions.values()
        ]

    async def review(self, tool_name: str, arguments: dict[str, Any], call_id: str) -> bool:
        if self.request.tool_executor is None:
            raise ValueError("MCP review requires a governed tool executor")
        review = await self.request.tool_executor.review_tool_call(
            context=self.request.context, tool_name=tool_name, arguments=arguments
        )
        self.approval_reviews[call_id] = review
        if review.get("decision") == "deny":
            raise ValueError("MCP tool was denied by policy")
        if review.get("decision") not in {"allow", "require_approval"}:
            raise ValueError("Invalid MCP tool approval decision")
        return review["decision"] == "require_approval"

    def invocation_meta(self, context: MCPToolMetaContext) -> dict[str, Any]:
        if not isinstance(context.run_context, ToolContext):
            raise ValueError("MCP invocation requires the SDK tool call identity")
        return {"opsmesh_tool_call_id": context.run_context.tool_call_id}

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        await raise_if_cancelled(self.request.cancellation)
        if tool_name not in self.definitions or self.request.tool_executor is None:
            raise ValueError("MCP tool is outside the frozen capability catalog")
        call_id = (meta or {}).get("opsmesh_tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("MCP invocation is missing its SDK tool call identity")
        result = await self.request.tool_executor.execute_tool(
            context=self.request.context,
            tool_name=tool_name,
            arguments=arguments or {},
            tool_call_id=call_id,
            approval_granted=True,
        )
        await raise_if_cancelled(self.request.cancellation)
        if (
            result.status == "failed"
            and result.output is not None
            and result.output.get("isError") is True
        ):
            return CallToolResult.model_validate(result.output)
        if result.status != "completed":
            raise RuntimeError("MCP Runtime execution failed")
        return CallToolResult.model_validate(result.output)

    async def list_prompts(self) -> ListPromptsResult:
        return ListPromptsResult(prompts=[])

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        raise ValueError("MCP prompts are outside the frozen capability catalog")


def runtime_mcp_servers(request: AgentRunRequest) -> list[MCPServer]:
    if request.tool_executor is None:
        return []
    return (
        [RuntimeMCPServer(request)]
        if any(tool.source == "mcp" for tool in request.context.tool_definitions)
        else []
    )


def governed_mcp_tools(agent: AgentBase[Any], tools: list[AgentTool]) -> list[AgentTool]:
    # SDK MCP approval omits arguments/call_id. Use its public agent extension to
    # attach argument-dependent governance to tools converted by the SDK.
    from backend.app.agents.execution.providers.openai.tools import tool_provenance_guardrail

    for tool in tools:
        if not isinstance(tool, FunctionTool):
            continue
        server = next(
            (
                item
                for item in agent.mcp_servers
                if isinstance(item, RuntimeMCPServer) and tool.name in item.definitions
            ),
            None,
        )
        if server is None:
            continue

        tool.needs_approval = _approval_callback(server, tool.name)
        tool.tool_input_guardrails = [
            tool_provenance_guardrail(tool.name, runtime_context=server.request.context)
        ]
    return tools


def _approval_callback(
    server: RuntimeMCPServer,
    name: str,
) -> Callable[[RunContextWrapper[Any], dict[str, Any], str], Awaitable[bool]]:
    async def needs_approval(
        context: RunContextWrapper[Any], arguments: dict[str, Any], call_id: str
    ) -> bool:
        return await server.review(name, arguments, call_id)

    return needs_approval


class GovernedAgent(Agent[Any]):
    async def get_mcp_tools(self, run_context: RunContextWrapper[Any]) -> list[AgentTool]:
        return governed_mcp_tools(self, await super().get_mcp_tools(run_context))


class GovernedSandboxAgent(SandboxAgent[Any]):
    async def get_mcp_tools(self, run_context: RunContextWrapper[Any]) -> list[AgentTool]:
        return governed_mcp_tools(self, await super().get_mcp_tools(run_context))
