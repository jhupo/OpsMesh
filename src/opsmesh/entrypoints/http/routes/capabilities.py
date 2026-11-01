"""Registered tools, deployed skills, remote MCP and knowledge operations."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.capabilities.api import (
    MCPServerDefinition,
    SkillDefinition,
    ToolDefinition,
)
from opsmesh.modules.workspaces.api import WorkspaceArtifact

from ..security import (
    CurrentUser,
    authorize,
    can_access,
    get_application,
    validate_secret_reference,
)
from .capability_versions import router as version_router
from .knowledge import router as knowledge_router

router = APIRouter(tags=["capabilities"])


class InvokeToolRequest(OpsMeshContract):
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = Field(min_length=1, max_length=200)


class PublishSkillRequest(OpsMeshContract):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    workspace_id: str
    directory: str = "."
    description: str = ""


class LoadSkillRequest(OpsMeshContract):
    workspace_id: str
    target: str = ".agents"


@router.post("/tools", status_code=201)
async def register_tool(
    body: ToolDefinition, request: Request, user: CurrentUser
) -> ToolDefinition:
    await authorize(request, user, "capability:manage", body.ref)
    app = get_application(request)
    if body.code_ref.startswith("resource:"):
        raise OpsMeshError(
            "CONFIG_INVALID", "Generated resource closures cannot be rebound by HTTP"
        )
    if body.code_ref.startswith("script:"):
        definition = await app.services.scripts.get(body.code_ref.removeprefix("script:"))
        if body.input_schema != definition.input_schema:
            raise OpsMeshError(
                "CONFIG_INVALID", "Script input schema differs from published version"
            )
    else:
        app.services.code_tools.resolve(body.code_ref)
    return await app.services.capability_management.register_tool(body, actor_id=user.id)


@router.get("/tools")
async def tools(request: Request, user: CurrentUser) -> list[ToolDefinition]:
    return [
        tool
        for tool in await get_application(request).services.capabilities.list_tools()
        if (not tool.allowed_principals or user.id in tool.allowed_principals or user.is_admin)
        and await can_access(request, user, "capability:read", tool.ref)
    ]


@router.get("/tools/{reference:path}")
async def get_tool(reference: str, request: Request, user: CurrentUser) -> ToolDefinition:
    await authorize(request, user, "capability:read", reference)
    tool = await get_application(request).services.capability_management.get_tool(
        reference, actor_id=user.id
    )
    if tool.allowed_principals and user.id not in tool.allowed_principals and not user.is_admin:
        raise OpsMeshError("FORBIDDEN", "Tool is not visible to this principal")
    return tool


@router.post("/tools/{reference:path}/invoke")
async def invoke(
    reference: str, body: InvokeToolRequest, request: Request, user: CurrentUser
) -> Any:
    await authorize(request, user, "tool:invoke", reference)
    return await get_application(request).services.tool_invoker.invoke(
        reference,
        body.arguments,
        principal_id=user.id,
        call_id=body.call_id,
    )


@router.post("/skills", status_code=201)
async def publish_skill(
    body: PublishSkillRequest, request: Request, user: CurrentUser
) -> SkillDefinition:
    await authorize(request, user, "capability:manage", f"{body.name}@{body.version}")
    app = get_application(request)
    await app.authorize_workspace(user.id, body.workspace_id)
    root = await app.services.workspaces.root_path(body.workspace_id)
    relative = PurePosixPath(body.directory)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in body.directory
        or ":" in body.directory
    ):
        raise OpsMeshError("INVALID_REQUEST", "Skill source must be workspace-relative")
    source = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise OpsMeshError("INVALID_REQUEST", "Skill source cannot traverse symbolic links")
    if not source.resolve().is_relative_to(root.resolve()) or not source.is_dir():
        raise OpsMeshError("INVALID_REQUEST", "Skill source directory is unavailable")
    return await app.services.skill_management.publish(
        body.name, body.version, source, actor_id=user.id, description=body.description
    )


@router.get("/skills/{reference:path}")
async def skill(reference: str, request: Request, user: CurrentUser) -> SkillDefinition:
    await authorize(request, user, "capability:read", reference)
    return await get_application(request).services.skill_management.get(reference, actor_id=user.id)


@router.get("/skills")
async def skills(request: Request, user: CurrentUser) -> tuple[SkillDefinition, ...]:
    return await get_application(request).services.skill_management.list(actor_id=user.id)


@router.post("/skills/{reference:path}/load")
async def load_skill(
    reference: str, body: LoadSkillRequest, request: Request, user: CurrentUser
) -> tuple[WorkspaceArtifact, ...]:
    await authorize(request, user, "capability:read", reference)
    app = get_application(request)
    await app.authorize_workspace(user.id, body.workspace_id, write=True)
    return await app.services.skill_management.load(
        reference, body.workspace_id, target=body.target, actor_id=user.id
    )


@router.post("/mcp", status_code=201)
async def register_mcp(
    body: MCPServerDefinition, request: Request, user: CurrentUser
) -> MCPServerDefinition:
    await authorize(request, user, "capability:manage", f"mcp:{body.name}@{body.version}")
    if body.transport not in {"streamable_http", "sse"} or body.execution_location != "remote":
        raise OpsMeshError(
            "CONFIG_INVALID", "HTTP can only register remote MCP; host processes require deployment"
        )
    url = urlsplit(body.url or "")
    if url.query or url.fragment:
        raise OpsMeshError(
            "CONFIG_INVALID", "Use MCP header secret references instead of URL credentials"
        )
    for reference in body.header_secret_refs.values():
        validate_secret_reference(reference)
    return await get_application(request).services.mcp_management.register(body, actor_id=user.id)


@router.get("/mcp/{reference:path}")
async def mcp(reference: str, request: Request, user: CurrentUser) -> MCPServerDefinition:
    await authorize(request, user, "capability:read", f"mcp:{reference}")
    return await get_application(request).services.mcp_management.get(reference, actor_id=user.id)


@router.get("/mcp")
async def mcp_servers(request: Request, user: CurrentUser) -> tuple[MCPServerDefinition, ...]:
    return await get_application(request).services.mcp_management.list(actor_id=user.id)


@router.post("/mcp/{reference:path}/discover")
async def discover_mcp(
    reference: str, request: Request, user: CurrentUser
) -> tuple[ToolDefinition, ...]:
    app = get_application(request)
    return await app.services.mcp_management.discover(reference, principal_id=user.id)


router.include_router(knowledge_router)
router.include_router(version_router)
