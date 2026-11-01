"""Publish pinned Git skills and immutable scripts from authorized workspaces."""

from fastapi import APIRouter, Request

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.capabilities.api import (
    GitSkillSource,
    ScriptToolDefinition,
    ScriptToolSource,
    SkillDefinition,
)

from ..security import CurrentUser, authorize, get_application
from ..workspace_sources import workspace_source

router = APIRouter(tags=["capabilities"])


@router.get("/scripts")
async def scripts(request: Request, user: CurrentUser) -> tuple[ScriptToolDefinition, ...]:
    return await get_application(request).services.script_management.list(actor_id=user.id)


class GitSkillPublishRequest(OpsMeshContract):
    name: str
    version: str
    source: GitSkillSource
    description: str = ""


class ScriptPublishRequest(OpsMeshContract):
    workspace_id: str
    directory: str = "."
    declaration: ScriptToolSource


@router.post("/skill-imports/git", status_code=201)
async def import_git_skill(
    body: GitSkillPublishRequest, request: Request, user: CurrentUser
) -> SkillDefinition:
    await authorize(request, user, "capability:manage", f"{body.name}@{body.version}")
    return await get_application(request).services.capability_management.publish_git(
        body.name,
        body.version,
        body.source,
        description=body.description,
        actor_id=user.id,
    )


@router.post("/scripts", status_code=201)
async def publish_script(
    body: ScriptPublishRequest, request: Request, user: CurrentUser
) -> ScriptToolDefinition:
    await authorize(
        request, user, "capability:manage", f"{body.declaration.name}@{body.declaration.version}"
    )
    source = await workspace_source(request, user, body.workspace_id, body.directory)
    return await get_application(request).services.capability_management.publish_script(
        source, body.declaration, actor_id=user.id
    )


@router.get("/scripts/{reference:path}")
async def script(reference: str, request: Request, user: CurrentUser) -> ScriptToolDefinition:
    await authorize(request, user, "capability:read", reference)
    return await get_application(request).services.script_management.get(
        reference, actor_id=user.id
    )
