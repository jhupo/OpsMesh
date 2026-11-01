"""Provider-hosted tools use admission policy; calls execute outside the local tool gateway."""

import inspect
from typing import Any

from agents import (
    CodeInterpreterTool,
    FileSearchTool,
    ImageGenerationTool,
    ToolSearchTool,
    WebSearchTool,
)
from agents.tool import ImageGenerationToolConfig
from openai.types.responses.file_search_tool_param import Filters, RankingOptions
from openai.types.responses.web_search_tool_param import UserLocation
from pydantic import TypeAdapter

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentExecutionEvent,
    AgentSpecification,
    CodeInterpreterBinding,
    FileSearchBinding,
    HostedMCPBinding,
    HostedToolBinding,
    ImageGenerationBinding,
    ToolSearchBinding,
    WebSearchBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import RuntimeExtensions

from .execution_state import execution_state
from .hosted_mcp import build_hosted_mcp


def map_hosted_tool(binding: HostedToolBinding) -> Any:
    if isinstance(binding, ToolSearchBinding):
        return ToolSearchTool(description=binding.description, execution="server")
    if isinstance(binding, WebSearchBinding):
        location = {
            name: getattr(binding, name)
            for name in ("country", "city", "region", "timezone")
            if getattr(binding, name) is not None
        }
        return WebSearchTool(
            search_context_size=binding.search_context_size,
            user_location=TypeAdapter(UserLocation).validate_python(
                {"type": "approximate", **location}
            )
            if location
            else None,
            filters={"allowed_domains": list(binding.allowed_domains)}
            if binding.allowed_domains
            else None,
            external_web_access=binding.external_web_access,
        )
    if isinstance(binding, FileSearchBinding):
        filter_type: Any = Filters
        return FileSearchTool(
            vector_store_ids=list(binding.vector_store_ids),
            max_num_results=binding.max_num_results,
            include_search_results=binding.include_search_results,
            filters=TypeAdapter(filter_type).validate_python(binding.filters)
            if binding.filters
            else None,
            ranking_options=TypeAdapter(RankingOptions).validate_python(binding.ranking_options)
            if binding.ranking_options
            else None,
        )
    if isinstance(binding, CodeInterpreterBinding):
        if binding.container_id and binding.file_ids:
            raise OpsMeshError(
                "CONFIG_INVALID",
                "Existing hosted containers cannot be combined with automatic file provisioning",
            )
        container: Any = binding.container_id or {
            "type": "auto",
            "file_ids": list(binding.file_ids),
            "memory_limit": binding.memory_limit,
        }
        return CodeInterpreterTool(tool_config={"type": "code_interpreter", "container": container})
    if isinstance(binding, ImageGenerationBinding):
        return ImageGenerationTool(
            tool_config=TypeAdapter(ImageGenerationToolConfig).validate_python(
                binding.model_dump(mode="json")
            )
        )
    raise OpsMeshError("FEATURE_UNSUPPORTED", "Provider-hosted tool kind is unsupported")


async def build_hosted_tools(
    spec: AgentSpecification,
    context: AgentExecutionContext,
    extensions: RuntimeExtensions | None = None,
) -> list[Any]:
    if not spec.hosted_tools:
        return []
    authorize = context.services.authorize_hosted_tool
    if authorize is None:
        raise OpsMeshError(
            "DEPENDENCY_UNAVAILABLE",
            "Provider-hosted tools require explicit admission authorization",
        )
    result = []
    for binding in spec.hosted_tools:
        admitted = authorize(binding)
        if inspect.isawaitable(admitted):
            await admitted
        if isinstance(binding, HostedMCPBinding):
            if extensions is None:
                raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Hosted MCP extensions are missing")
            result.append(await build_hosted_mcp(binding, context, extensions))
        else:
            result.append(map_hosted_tool(binding))
    emit = execution_state(context).backend_event_sink
    if emit is not None:
        await emit(
            AgentExecutionEvent(
                kind="hosted_tools_admitted",
                payload={
                    "agent_ref": spec.ref,
                    "tools": [binding.type for binding in spec.hosted_tools],
                    "execution_location": "provider",
                    "authorization": "admission",
                    "local_gateway_per_call": False,
                },
            )
        )
    return result
