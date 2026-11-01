"""Workspace paths remain relative and ownership is resolved by the application bridge."""

from fastapi import APIRouter, Request, Response
from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.workspaces.api import (
    WorkspaceArtifact,
    WorkspaceDescriptor,
    WorkspaceFileInfo,
    WorkspaceManifest,
    WorkspaceSnapshot,
)

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100)


class RestoreWorkspaceRequest(OpsMeshContract):
    snapshot_id: str
    target_workspace_id: str


@router.post("", status_code=201)
async def create(
    body: CreateWorkspaceRequest, request: Request, user: CurrentUser
) -> WorkspaceDescriptor:
    return await get_application(request).services.workspace_management.create(
        body.id, actor_id=user.id
    )


@router.post("/restore", status_code=201)
async def restore(
    body: RestoreWorkspaceRequest, request: Request, user: CurrentUser
) -> WorkspaceDescriptor:
    return await get_application(request).services.workspace_management.restore(
        actor_id=user.id,
        snapshot_id=body.snapshot_id,
        target_workspace_id=body.target_workspace_id,
    )


@router.get("/{workspace_id}")
async def workspace(workspace_id: str, request: Request, user: CurrentUser) -> WorkspaceDescriptor:
    app = get_application(request)
    await app.authorize_workspace(user.id, workspace_id)
    return await app.services.workspace_management.get(workspace_id, actor_id=user.id)


@router.delete("/{workspace_id}", status_code=204)
async def delete(workspace_id: str, request: Request, user: CurrentUser) -> Response:
    app = get_application(request)
    await app.authorize_workspace(user.id, workspace_id, write=True)
    await app.services.workspace_management.delete(workspace_id, actor_id=user.id)
    return Response(status_code=204)


@router.put("/{workspace_id}/files/{path:path}")
async def write_file(
    workspace_id: str, path: str, request: Request, user: CurrentUser
) -> WorkspaceArtifact:
    app = get_application(request)
    await app.authorize_workspace(user.id, workspace_id, write=True)
    return await app.services.workspace_management.write(
        workspace_id, path, await request.body(), actor_id=user.id
    )


@router.get("/{workspace_id}/files/{path:path}")
async def read_file(workspace_id: str, path: str, request: Request, user: CurrentUser) -> Response:
    app = get_application(request)
    await app.authorize_workspace(user.id, workspace_id)
    data = await app.services.workspace_management.read(workspace_id, path, actor_id=user.id)
    return Response(
        data, media_type="application/octet-stream", headers={"Content-Disposition": "attachment"}
    )


@router.post("/{workspace_id}/materialize")
async def materialize(
    workspace_id: str, body: WorkspaceManifest, request: Request, user: CurrentUser
) -> tuple[WorkspaceArtifact, ...]:
    app = get_application(request)
    await app.authorize_workspace(user.id, workspace_id, write=True)
    if any(entry.digest is not None for entry in body.entries):
        raise OpsMeshError(
            "INVALID_REQUEST",
            "HTTP manifests require inline content; use authorized snapshots to restore",
        )
    return await app.services.workspace_management.materialize(workspace_id, body, actor_id=user.id)


@router.post("/{workspace_id}/snapshots", status_code=201)
async def snapshot(workspace_id: str, request: Request, user: CurrentUser) -> WorkspaceSnapshot:
    app = get_application(request)
    await app.authorize_workspace(user.id, workspace_id)
    return await app.services.workspace_management.snapshot(workspace_id, actor_id=user.id)


class RenameWorkspaceEntryRequest(OpsMeshContract):
    source: str
    target: str


@router.get("")
async def list_workspaces(request: Request, user: CurrentUser) -> tuple[WorkspaceDescriptor, ...]:
    return await get_application(request).services.workspace_management.list(actor_id=user.id)


@router.get("/{workspace_id}/tree")
async def tree(
    workspace_id: str, request: Request, user: CurrentUser
) -> tuple[WorkspaceFileInfo, ...]:
    return await get_application(request).services.workspace_management.tree(
        workspace_id, actor_id=user.id
    )


@router.get("/{workspace_id}/stat/{path:path}")
async def stat_file(
    workspace_id: str, path: str, request: Request, user: CurrentUser
) -> WorkspaceFileInfo:
    return await get_application(request).services.workspace_management.stat(
        workspace_id, path, actor_id=user.id
    )


@router.delete("/{workspace_id}/files/{path:path}", status_code=204)
async def delete_file(
    workspace_id: str, path: str, request: Request, user: CurrentUser
) -> Response:
    await get_application(request).services.workspace_management.delete_file(
        workspace_id, path, actor_id=user.id
    )
    return Response(status_code=204)


@router.post("/{workspace_id}/rename")
async def rename(
    workspace_id: str, body: RenameWorkspaceEntryRequest, request: Request, user: CurrentUser
) -> WorkspaceFileInfo:
    return await get_application(request).services.workspace_management.rename(
        workspace_id, body.source, body.target, actor_id=user.id
    )


@router.get("/{workspace_id}/snapshots")
async def snapshots(
    workspace_id: str, request: Request, user: CurrentUser
) -> tuple[WorkspaceSnapshot, ...]:
    return await get_application(request).services.workspace_management.list_snapshots(
        workspace_id, actor_id=user.id
    )


@router.delete("/{workspace_id}/snapshots/{snapshot_id}", status_code=204)
async def delete_snapshot(
    workspace_id: str, snapshot_id: str, request: Request, user: CurrentUser
) -> Response:
    service = get_application(request).services.workspace_management
    snapshot = await service.get_snapshot(snapshot_id, actor_id=user.id)
    if snapshot.workspace_id != workspace_id:
        raise OpsMeshError("NOT_FOUND", "Workspace snapshot not found")
    await service.delete_snapshot(snapshot_id, actor_id=user.id)
    return Response(status_code=204)
