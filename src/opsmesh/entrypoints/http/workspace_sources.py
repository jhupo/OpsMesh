"""Resolve authorized source directories without exposing arbitrary host paths."""

from pathlib import Path, PurePosixPath

from fastapi import Request

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import UserPrincipal

from .security import get_application


async def workspace_source(
    request: Request, user: UserPrincipal, workspace_id: str, directory: str
) -> Path:
    app = get_application(request)
    await app.authorize_workspace(user.id, workspace_id)
    root = await app.services.workspaces.root_path(workspace_id)
    relative = PurePosixPath(directory)
    if relative.is_absolute() or ".." in relative.parts or "\\" in directory or ":" in directory:
        raise OpsMeshError("INVALID_REQUEST", "Source must be workspace-relative")
    source = root
    for part in relative.parts:
        source /= part
        if source.is_symlink():
            raise OpsMeshError("INVALID_REQUEST", "Source cannot traverse symbolic links")
    if not source.resolve().is_relative_to(root.resolve()) or not source.is_dir():
        raise OpsMeshError("INVALID_REQUEST", "Source directory is unavailable")
    return source
