"""Use official durable MCP proxies; add only product authorization callbacks."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from agents import Agent, FunctionTool, RunContextWrapper
from agents import Tool as AgentTool
from agents.agent import AgentBase
from agents.mcp import MCPServer
from agents.mcp.util import MCPToolMetaContext
from agents.sandbox import SandboxAgent
from agents.tool_context import ToolContext
from temporalio.common import RetryPolicy
from temporalio.openai_agents.workflow import temporal_mcp_server
from temporalio.workflow import ActivityCancellationType

from opsmesh.agents.execution.contracts import AgentReference, AgentRunRequest
from opsmesh.agents.execution.providers.openai.tools import tool_provenance_guardrail


def invocation_meta(context: MCPToolMetaContext) -> dict[str, Any]:
    if not isinstance(context.run_context, ToolContext):
        raise ValueError("MCP invocation requires the SDK tool call identity")
    return {"opsmesh_tool_call_id": context.run_context.tool_call_id}


def runtime_mcp_servers(request: AgentRunRequest) -> list[MCPServer]:
    grouped: dict[str, list[str]] = {}
    for tool in request.context.tool_definitions:
        if tool.source != "mcp":
            continue
        if tool.mcp_server_id is None:
            raise ValueError("MCP tool has no authorized server reference")
        grouped.setdefault(str(tool.mcp_server_id), []).append(tool.name)
    return [
        temporal_mcp_server(
            "opsmesh",
            factory_argument={
                "workspace_id": str(request.context.workspace_id),
                "run_id": str(request.context.run_id),
                "server_id": server_id,
                "agent_profile_id": str(request.agent_profile.id),
                "capability_catalog_fingerprint": request.context.metadata.get(
                    "capability_catalog_fingerprint"
                ),
            },
            activity_config={
                "start_to_close_timeout": timedelta(minutes=30),
                "retry_policy": RetryPolicy(maximum_attempts=1),
                "cancellation_type": ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            },
            tool_filter=_allowlisted_tools(tuple(grouped[server_id])),
            tool_meta_resolver=invocation_meta,
            require_approval="never",
            failure_error_function=None,
        )
        for server_id in grouped
    ]


def _allowlisted_tools(names: tuple[str, ...]) -> Callable[[Any, Any], bool]:
    allowed = frozenset(names)

    def include_tool(_: Any, tool: Any) -> bool:
        return getattr(tool, "name", None) in allowed

    return include_tool


def governed_mcp_tools(agent: AgentBase[Any], tools: list[AgentTool]) -> list[AgentTool]:
    if not isinstance(agent, GovernedAgent | GovernedSandboxAgent):
        raise ValueError("MCP agent is outside the authorized graph")
    request = agent.runtime_request
    if request is None:
        raise ValueError("MCP agent has no authorized definition")
    definitions = {item.name for item in request.context.tool_definitions if item.source == "mcp"}
    for tool in tools:
        if not isinstance(tool, FunctionTool) or tool.name not in definitions:
            raise ValueError("MCP tool is outside the frozen capability catalog")
        tool.needs_approval = _approval_callback(agent, request, tool.name)
        tool.tool_input_guardrails = [
            tool_provenance_guardrail(tool.name, runtime_context=request.context)
        ]
    return tools


def _approval_callback(
    agent: "GovernedAgent | GovernedSandboxAgent",
    request: AgentRunRequest,
    name: str,
) -> Callable[[RunContextWrapper[Any], dict[str, Any], str], Awaitable[bool]]:
    async def needs_approval(
        context: RunContextWrapper[Any], arguments: dict[str, Any], call_id: str
    ) -> bool:
        if request.tool_executor is None:
            raise ValueError("MCP approval requires an authorized tool Activity")
        review = await request.tool_executor.review_tool_call(
            context=request.context, tool_name=name, arguments=arguments
        )
        agent.mcp_approval_reviews[call_id] = review
        if review.get("decision") not in {"allow", "require_approval"}:
            raise ValueError("MCP tool was denied by policy")
        return review["decision"] == "require_approval"

    return needs_approval


@dataclass
class GovernedAgent(Agent[Any]):
    runtime_request: AgentRunRequest | None = field(default=None, repr=False)
    runtime_ref: AgentReference | None = None
    mcp_approval_reviews: dict[str, dict[str, object]] = field(default_factory=dict, repr=False)

    async def get_mcp_tools(self, run_context: RunContextWrapper[Any]) -> list[AgentTool]:
        return governed_mcp_tools(self, await super().get_mcp_tools(run_context))


@dataclass
class GovernedSandboxAgent(SandboxAgent[Any]):
    runtime_request: AgentRunRequest | None = field(default=None, repr=False)
    runtime_ref: AgentReference | None = None
    mcp_approval_reviews: dict[str, dict[str, object]] = field(default_factory=dict, repr=False)
    sandbox_approval_reviews: dict[str, dict[str, object]] = field(default_factory=dict, repr=False)

    async def get_mcp_tools(self, run_context: RunContextWrapper[Any]) -> list[AgentTool]:
        return governed_mcp_tools(self, await super().get_mcp_tools(run_context))
