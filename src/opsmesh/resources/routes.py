import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.contracts import UserIdentity
from opsmesh.sessions.sandboxes import SandboxStorage
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.users.service import require_owner

from .files import LocalFiles, SandboxFiles
from .models import Artifact, Workspace
from .schemas import CreateWorkspaceRequest, FileEntryView, StorageKind, WorkspaceView
from .workspaces import owned

router = APIRouter(tags=["resources"])


@asynccontextmanager
async def filesystem(
    request: Request, user: UserIdentity, kind: StorageKind, root_id: str, *, mutate: bool = False
) -> AsyncIterator[LocalFiles | SandboxFiles]:
    app = get_application(request)
    if kind == "workspace":
        with app.sessions.begin() as db:
            workspace = owned(db, user, root_id)
            db.refresh(workspace, with_for_update=True)
            yield LocalFiles(db, app.settings.data_dir, workspace, app.settings.file_limit_bytes)
    else:
        storage = SandboxStorage(app.sessions, app.cipher, app.registry, app.settings.data_dir)
        async with storage.open(user, root_id, mutate=mutate) as live:
            yield SandboxFiles(live, storage, root_id, app.settings.file_limit_bytes)


@router.post("/workspaces", status_code=201, response_model=WorkspaceView)
def create_workspace(
    body: CreateWorkspaceRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = Workspace(owner_id=user.user_id, name=body.name)
    db.add(row)
    db.flush()
    return serialize_record(row)


@router.get("/workspaces", response_model=list[WorkspaceView])
def listing(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    return [
        serialize_record(row)
        for row in db.scalars(
            select(Workspace).where(Workspace.owner_id == user.user_id).limit(200)
        )
    ]


@router.get("/files", response_model=list[FileEntryView])
def list_files(
    kind: StorageKind,
    root_id: str,
    request: Request,
    path: str = "",
    user: UserIdentity = Depends(get_current_user),
) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        async with filesystem(request, user, kind, root_id) as files:
            return await files.list(path)

    return asyncio.run(run())


@router.get("/file")
def read_file(
    kind: StorageKind,
    root_id: str,
    path: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
) -> Response:
    async def run() -> bytes:
        async with filesystem(request, user, kind, root_id) as files:
            return await files.read(path)

    return Response(asyncio.run(run()), media_type="application/octet-stream")


@router.put("/file", status_code=204)
def write_file(
    kind: StorageKind,
    root_id: str,
    path: str,
    request: Request,
    content: UploadFile = File(),
    user: UserIdentity = Depends(get_current_user),
) -> None:
    value = content.file.read(get_application(request).settings.file_limit_bytes + 1)
    if len(value) > get_application(request).settings.file_limit_bytes:
        raise ValueError("File exceeds the upload limit")

    async def run() -> None:
        async with filesystem(request, user, kind, root_id, mutate=True) as files:
            await files.write(path, value)

    asyncio.run(run())


@router.delete("/file", status_code=204)
def remove_file(
    kind: StorageKind,
    root_id: str,
    path: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
) -> None:
    async def run() -> None:
        async with filesystem(request, user, kind, root_id, mutate=True) as files:
            await files.remove(path)

    asyncio.run(run())


@router.post("/directory", status_code=204)
def create_directory(
    kind: StorageKind,
    root_id: str,
    path: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
) -> None:
    async def run() -> None:
        async with filesystem(request, user, kind, root_id, mutate=True) as files:
            await files.mkdir(path)

    asyncio.run(run())


@router.get("/artifacts/{artifact_id}")
def artifact(
    artifact_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> FileResponse:
    row = db.get(Artifact, artifact_id)
    if row is None:
        raise NotFoundError("Artifact not found")
    require_owner(user, row.owner_id)
    path = (
        Path(get_application(request).settings.data_dir)
        / "users"
        / row.owner_id
        / "artifacts"
        / row.id
    )
    if not path.is_file():
        raise NotFoundError("Artifact file not found")
    return FileResponse(path, filename=row.name, media_type="application/octet-stream")
