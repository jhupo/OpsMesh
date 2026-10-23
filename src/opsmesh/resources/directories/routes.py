"""Persistent directory management and user-facing file transfer APIs."""

from hashlib import sha256
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from opsmesh.resources.directories import service
from opsmesh.resources.directories.schemas import (
    DirectoryCreateRequest,
    DirectoryUpdateRequest,
    DirectoryView,
    FileEntryView,
    GrantRequest,
    GrantView,
)
from opsmesh.resources.storage.base import FileStorage
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/resources/directories", tags=["resources"])
Pagination = Annotated[PageParams, Depends(pagination_params)]
FilePath = Annotated[str, Query(min_length=1, max_length=1000)]


def get_filesystem(request: Request) -> FileStorage:
    filesystem = getattr(request.app.state, "resource_filesystem", None)
    if not isinstance(filesystem, FileStorage):
        raise HTTPException(status_code=503, detail="Resource storage is not configured")
    return filesystem


Filesystem = Annotated[FileStorage, Depends(get_filesystem)]


@router.post("", status_code=201, operation_id="resources.directories.create")
def create_directory(
    payload: DirectoryCreateRequest, session: DbSession, user: CurrentUserDep
) -> DirectoryView:
    with session_transaction(session):
        directory = service.create_directory(session, actor=user, name=payload.name)
        result = DirectoryView.model_validate(directory)
    return result


@router.post("/default", operation_id="resources.directories.ensure_default")
def ensure_default_directory(session: DbSession, user: CurrentUserDep) -> DirectoryView:
    with session_transaction(session):
        result = DirectoryView.model_validate(service.ensure_default_directory(session, actor=user))
    return result


@router.get("", operation_id="resources.directories.list")
def list_directories(
    session: DbSession, user: CurrentUserDep, page: Pagination
) -> PageResponse[DirectoryView]:
    directories, total = service.list_directories(session, actor=user, page=page)
    return page_response(directories, total, page, DirectoryView)


@router.get("/{directory_id}", operation_id="resources.directories.read")
def read_directory(directory_id: UUID, session: DbSession, user: CurrentUserDep) -> DirectoryView:
    return DirectoryView.model_validate(
        service.get_directory(session, actor=user, directory_id=directory_id)
    )


@router.patch("/{directory_id}", operation_id="resources.directories.rename")
def rename_directory(
    directory_id: UUID,
    payload: DirectoryUpdateRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> DirectoryView:
    with session_transaction(session):
        result = DirectoryView.model_validate(
            service.rename_directory(
                session, actor=user, directory_id=directory_id, name=payload.name
            )
        )
    return result


@router.delete("/{directory_id}", status_code=204, operation_id="resources.directories.delete")
def delete_directory(directory_id: UUID, session: DbSession, user: CurrentUserDep) -> Response:
    """Unregister the directory and revoke grants; persistent contents are retained."""
    with session_transaction(session):
        service.delete_directory(session, actor=user, directory_id=directory_id)
    return Response(status_code=204)


@router.get("/{directory_id}/grants", operation_id="resources.directories.grants.list")
def list_grants(
    directory_id: UUID, session: DbSession, user: CurrentUserDep, page: Pagination
) -> PageResponse[GrantView]:
    grants, total = service.list_grants(session, actor=user, directory_id=directory_id, page=page)
    return page_response(grants, total, page, GrantView)


@router.put("/{directory_id}/grants/{user_id}", operation_id="resources.directories.grants.set")
def set_grant(
    directory_id: UUID,
    user_id: UUID,
    payload: GrantRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> GrantView:
    with session_transaction(session):
        result = GrantView.model_validate(
            service.set_grant(
                session,
                actor=user,
                directory_id=directory_id,
                grantee_user_id=user_id,
                permissions=list(payload.permissions),
            )
        )
    return result


@router.delete(
    "/{directory_id}/grants/{user_id}",
    status_code=204,
    operation_id="resources.directories.grants.revoke",
)
def revoke_grant(
    directory_id: UUID, user_id: UUID, session: DbSession, user: CurrentUserDep
) -> Response:
    with session_transaction(session):
        service.revoke_grant(
            session, actor=user, directory_id=directory_id, grantee_user_id=user_id
        )
    return Response(status_code=204)


@router.get("/{directory_id}/entries", operation_id="resources.files.list")
def list_files(
    directory_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    filesystem: Filesystem,
    page: Pagination,
    path: Annotated[str, Query(max_length=1000)] = "",
) -> PageResponse[FileEntryView]:
    entries, total = service.list_files(
        session, actor=user, directory_id=directory_id, filesystem=filesystem, path=path, page=page
    )
    return page_response(entries, total, page, FileEntryView)


@router.get("/{directory_id}/content", operation_id="resources.files.download")
def download_file(
    directory_id: UUID,
    path: FilePath,
    session: DbSession,
    user: CurrentUserDep,
    filesystem: Filesystem,
) -> Response:
    content = service.read_file(
        session, actor=user, directory_id=directory_id, filesystem=filesystem, path=path
    )
    filename = quote(PurePosixPath(path).name, safe="")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{sha256(content).hexdigest()}"',
        },
    )


@router.put("/{directory_id}/content", status_code=204, operation_id="resources.files.upload")
async def upload_file(
    directory_id: UUID,
    path: FilePath,
    request: Request,
    session: DbSession,
    user: CurrentUserDep,
    filesystem: Filesystem,
) -> Response:
    def preflight() -> None:
        with session_transaction(session):
            service.get_directory(
                session, actor=user, directory_id=directory_id, permission="write"
            )

    # Release the authentication read transaction before waiting for the request body.
    await run_in_threadpool(preflight)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > filesystem.max_transfer_bytes:
            raise InvalidArgumentError("The file exceeds the transfer limit")
        content.extend(chunk)

    def store() -> None:
        with session_transaction(session):
            service.write_file(
                session,
                actor=user,
                directory_id=directory_id,
                filesystem=filesystem,
                path=path,
                content=bytes(content),
            )

    await run_in_threadpool(store)
    return Response(status_code=204)


@router.post("/{directory_id}/entries", status_code=204, operation_id="resources.files.mkdir")
def create_subdirectory(
    directory_id: UUID,
    path: FilePath,
    session: DbSession,
    user: CurrentUserDep,
    filesystem: Filesystem,
) -> Response:
    with session_transaction(session):
        service.create_subdirectory(
            session, actor=user, directory_id=directory_id, filesystem=filesystem, path=path
        )
    return Response(status_code=204)


@router.delete("/{directory_id}/entries", status_code=204, operation_id="resources.files.delete")
def delete_entry(
    directory_id: UUID,
    path: FilePath,
    session: DbSession,
    user: CurrentUserDep,
    filesystem: Filesystem,
) -> Response:
    """Delete a file, symlink or empty subdirectory."""
    with session_transaction(session):
        service.delete_entry(
            session, actor=user, directory_id=directory_id, filesystem=filesystem, path=path
        )
    return Response(status_code=204)
