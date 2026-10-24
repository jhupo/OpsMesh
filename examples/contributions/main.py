import os
from typing import Any
from urllib.parse import urlsplit

from agents import RunContextWrapper, function_tool
from agents.mcp import MCPServerStreamableHttp
from agents.mcp.util import MCPToolMetaContext
from opsmesh_sdk import InvocationContext, PluginContext


@function_tool(failure_error_function=None)
async def describe_caller(context: RunContextWrapper[InvocationContext]) -> dict[str, Any]:
    """Read the caller's trusted identity and caller-supplied business metadata."""
    user = context.context.user
    if "user" not in user.roles and not user.is_admin:
        raise PermissionError("This tool requires a platform user role")
    return {
        "user_id": user.user_id,
        "group_ids": sorted(user.group_ids),
        "business_reference": context.context.metadata.get("business_reference"),
    }


def business_mcp(context: InvocationContext) -> MCPServerStreamableHttp:
    url = str(context.config["url"])
    parsed = urlsplit(url)
    allowed = set(filter(None, os.environ.get("EXAMPLE_MCP_ALLOWED_HOSTS", "").split(",")))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise ValueError("MCP endpoint is not enabled by the plugin administrator")

    async def metadata(data: MCPToolMetaContext) -> dict[str, Any]:
        user = data.run_context.context.user
        return {
            "user_id": user.user_id,
            "roles": sorted(user.roles),
            "group_ids": sorted(user.group_ids),
            "task_id": context.task_id,
        }

    return MCPServerStreamableHttp(
        name="business",
        params={"url": url},
        tool_meta_resolver=metadata,
        require_approval="never",
        max_retry_attempts=0,
    )


def setup(context: PluginContext) -> None:
    context.tool("describe-caller", describe_caller)
    context.mcp("business", business_mcp)
    context.skill("reporting", directory="skills/reporting")
