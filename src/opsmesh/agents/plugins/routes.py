"""Authenticated discovery of setup registrations and explicit tool invocation."""

from uuid import uuid4

from fastapi import APIRouter, Request
from opsmesh_sdk import PluginManifest
from opsmesh_sdk.hooks import HookPoint
from opsmesh_sdk.registration import ConfigField
from pydantic import BaseModel, ConfigDict, Field

from opsmesh.agents.plugins.registry import PluginRegistry, invoke_registered_tool
from opsmesh.agents.tools import ToolInfo
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.users.auth.dependencies import CurrentUserDep

router = APIRouter(prefix="/agents/plugins", tags=["agent-plugins"])


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, object]
    config: dict[str, object] = Field(default_factory=dict)


class PluginToolView(ToolInfo):
    tool_name: str
    config_fields: list[ConfigField]


class PluginView(PluginManifest):
    tools: list[PluginToolView]
    skills: list[str]
    agent_skill_ids: list[str]
    agent_hooks_id: str | None
    hook_points: list[HookPoint]
    mcps: list[dict[str, object]]


@router.get("", operation_id="agents.plugins.list")
def list_plugins(request: Request, user: CurrentUserDep) -> list[PluginView]:
    registry: PluginRegistry = request.app.state.agent_plugins
    admin = user.has_platform_role("admin")
    return [
        PluginView(
            **context.manifest.model_dump(),
            tools=[
                PluginToolView(
                    **tool.describe(plugin_id).model_dump(),
                    tool_name=tool.registration.name,
                    config_fields=list(tool.registration.config_fields),
                )
                for (plugin_id, _), tool in registry.tools.items()
                if plugin_id == context.manifest.id and (admin or not tool.registration.admin_only)
            ],
            skills=sorted(path.name for path in context.skills_directory.iterdir())
            if context.skills_directory
            else [],
            agent_skill_ids=[
                f"{context.manifest.id}/skills/{path.name}"
                for path in sorted(context.skills_directory.iterdir())
            ]
            if context.skills_directory
            else [],
            agent_hooks_id=f"{context.manifest.id}/hooks" if context.hooks else None,
            hook_points=sorted({item.point for item in context.hooks}),
            mcps=[
                {
                    "id": f"{context.manifest.id}/mcp/{item.name}",
                    "description": item.description,
                    "config_fields": [value.model_dump() for value in item.config_fields],
                    "admin_only": item.admin_only,
                }
                for item in context.mcps.values()
                if admin or not item.admin_only
            ],
        )
        for context in registry.plugins.values()
    ]


@router.post("/{plugin_id}/tools/{name}", operation_id="agents.plugins.invoke")
async def invoke_tool(
    plugin_id: str, name: str, body: ToolArguments, request: Request, user: CurrentUserDep
) -> dict[str, object]:
    registry: PluginRegistry = request.app.state.agent_plugins
    tool = registry.tools.get((plugin_id, name))
    if tool is None:
        raise NotFoundError("Plugin tool not found")
    if tool.registration.requires_approval:
        raise ConflictError("This tool requires an approved Agent or Workflow Run")
    operation_id = uuid4()
    value = await invoke_registered_tool(
        registry, plugin_id, name, body.arguments, user, operation_id, body.config
    )
    return {"operation_id": str(operation_id), "value": value}
