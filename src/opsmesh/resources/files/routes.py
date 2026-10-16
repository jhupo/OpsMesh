"""File, directory, grant and runtime policy APIs.

The module never imports identity: the reader is declared as a placeholder
dependency that bootstrap replaces with the assembled identity channel,
and the scope admission and storage ledger are read from the assembled
application state. Until then every route answers 503 instead of trusting
an unauthenticated caller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor
from opsmesh.resources.files import service
from opsmesh.resources.files.access import (
    RejectingScopeAdmission,
    ResourceReader,
    ScopeAdmission,
    ScopeRef,
    StorageQuotaLedger,
)
from opsmesh.resources.files.content import content_download_response
from opsmesh.resources.files.models import (
    ResourceDirectory,
    ResourceFile,
    ResourceFileVersion,
    ResourceRuntimePolicy,
)
from opsmesh.resources.files.schemas import (
    DirectoryCreateRequest,
    DirectoryView,
    FileCreateRequest,
    FilePage,
    FileVersionView,
    FileView,
    GrantCreateRequest,
    GrantView,
    RuntimePolicyRequest,
    RuntimePolicyView,
    VersionDiffView,
)
from opsmesh.resources.files.versions import list_versions, read_version, version_diff
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(prefix="/resources/files", tags=["resources"])

DbSession = Annotated[Session, Depends(get_db_session)]


def require_resource_reader() -> ResourceReader:
    """Placeholder for the assembled identity reader; bootstrap injects it."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Resource reading is not assembled",
    )


ReaderDep = Annotated[ResourceReader, Depends(require_resource_reader)]


def _scope_admission(request: Request) -> ScopeAdmission:
    admission = getattr(request.app.state, "scope_admission", None)
    if isinstance(admission, ScopeAdmission):
        return admission
    return RejectingScopeAdmission()


def _quota_ledger(request: Request) -> StorageQuotaLedger:
    ledger = getattr(request.app.state, "storage_quota_ledger", None)
    if isinstance(ledger, StorageQuotaLedger):
        return ledger
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The storage quota ledger is not assembled",
    )


def _storage_root(request: Request) -> Path:
    root = getattr(request.app.state, "storage_root", None)
    if not isinstance(root, str) or not root:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resource storage is not assembled",
        )
    return Path(root)


def _reader_actor(reader: ResourceReader) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(reader.user_id))


def _directory_view(directory: ResourceDirectory) -> DirectoryView:
    return DirectoryView(
        directory_id=directory.id,
        path=directory.path,
        parent_id=directory.parent_id,
        created_at=directory.created_at,
    )


def _file_view(resource: ResourceFile, latest_version: int) -> FileView:
    return FileView(
        file_id=resource.id,
        directory_id=resource.directory_id,
        name=resource.name,
        owner_user_id=resource.owner_user_id,
        latest_version=latest_version,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )


def _version_view(version: ResourceFileVersion) -> FileVersionView:
    return FileVersionView(
        file_id=version.file_id,
        version=version.version,
        digest=version.digest,
        size=version.size,
        media_type=version.media_type,
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at,
    )


@router.post(
    "/directories",
    response_model=DirectoryView,
    status_code=status.HTTP_201_CREATED,
    operation_id="resources.files.directories.create",
)
def create_directory(
    payload: DirectoryCreateRequest,
    session: DbSession,
    reader: ReaderDep,
    request: Request,
) -> DirectoryView:
    """Create one user directory at a validated relative path."""
    with session_transaction(session):
        directory = service.create_directory(
            session,
            owner_id=reader.user_id,
            path=payload.path,
            admission=_scope_admission(request),
            reader=reader,
            scope=ScopeRef(),
        )
    return _directory_view(directory)


@router.get(
    "/directories",
    response_model=list[DirectoryView],
    operation_id="resources.files.directories.list",
)
def list_directories(session: DbSession, reader: ReaderDep) -> list[DirectoryView]:
    directories = service.list_directories(session, owner_id=reader.user_id)
    return [_directory_view(directory) for directory in directories]


@router.delete(
    "/directories/{directory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="resources.files.directories.delete",
)
def delete_directory(directory_id: UUID, session: DbSession, reader: ReaderDep) -> Response:
    with session_transaction(session):
        service.delete_directory(session, directory_id=directory_id, reader=reader)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "",
    response_model=FileView,
    status_code=status.HTTP_201_CREATED,
    operation_id="resources.files.create",
)
def create_file(payload: FileCreateRequest, session: DbSession, reader: ReaderDep) -> FileView:
    with session_transaction(session):
        resource = service.create_file(
            session,
            owner_id=reader.user_id,
            directory_id=payload.directory_id,
            name=payload.name,
            reader=reader,
        )
    return _file_view(resource, latest_version=0)


@router.get("", response_model=FilePage, operation_id="resources.files.list")
def list_files(
    session: DbSession,
    reader: ReaderDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FilePage:
    files = service.list_files(session, reader=reader)
    page = PageParams(limit=limit, offset=offset)
    items = [
        _file_view(
            resource,
            latest_version=service.latest_version_number(session, file_id=resource.id),
        )
        for resource in files[page.offset : page.offset + page.limit]
    ]
    return FilePage(items=items, total=len(files))


@router.get("/{file_id}", response_model=FileView, operation_id="resources.files.read")
def read_file(file_id: UUID, session: DbSession, reader: ReaderDep) -> FileView:
    resource, _ = service.get_file(session, file_id=file_id, reader=reader)
    return _file_view(
        resource, latest_version=service.latest_version_number(session, file_id=resource.id)
    )


@router.delete(
    "/{file_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="resources.files.delete"
)
def delete_file(file_id: UUID, session: DbSession, reader: ReaderDep, request: Request) -> Response:
    with session_transaction(session):
        service.delete_file(
            session, file_id=file_id, reader=reader, storage_root=_storage_root(request)
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{file_id}/content",
    response_model=FileVersionView,
    operation_id="resources.files.content.write",
)
def write_file_content(
    file_id: UUID,
    session: DbSession,
    reader: ReaderDep,
    request: Request,
    expected_version: Annotated[int | None, Query(ge=1)] = None,
    media_type: Annotated[str | None, Query(max_length=200)] = None,
    idempotency_key: Annotated[str | None, Query(max_length=256)] = None,
    body: Annotated[bytes, Body()] = b"",
) -> FileVersionView:
    """Store one new version, optionally under a compare-and-set expectation."""
    with session_transaction(session):
        if expected_version is None:
            version = service.upload_version(
                session,
                file_id=file_id,
                content=body,
                media_type=media_type,
                reader=reader,
                actor=_reader_actor(reader),
                ledger=_quota_ledger(request),
                storage_root=_storage_root(request),
                idempotency_key=idempotency_key,
            )
        else:
            version = service.write_back(
                session,
                file_id=file_id,
                expected_version=expected_version,
                content=body,
                media_type=media_type,
                reader=reader,
                actor=_reader_actor(reader),
                ledger=_quota_ledger(request),
                storage_root=_storage_root(request),
                idempotency_key=idempotency_key,
            )
    return _version_view(version)


@router.get("/{file_id}/content", operation_id="resources.files.content.read")
def read_file_content(
    file_id: UUID, session: DbSession, reader: ReaderDep, request: Request
) -> Response:
    """Download the current version's verified bytes."""
    resource, _ = service.get_file(session, file_id=file_id, reader=reader)
    latest = service.latest_version_number(session, file_id=file_id)
    if latest == 0:
        raise NotFoundError("The file has no version yet")
    version, payload = service.read_version_content(
        session,
        file_id=file_id,
        version_number=latest,
        reader=reader,
        storage_root=_storage_root(request),
    )
    return content_download_response(
        payload, filename=resource.name, media_type=version.media_type
    )


@router.get(
    "/{file_id}/versions",
    response_model=list[FileVersionView],
    operation_id="resources.files.versions.list",
)
def list_file_versions(
    file_id: UUID, session: DbSession, reader: ReaderDep
) -> list[FileVersionView]:
    versions = list_versions(session, file_id=file_id, reader=reader)
    return [_version_view(version) for version in versions]


@router.get(
    "/{file_id}/versions/{version_number}",
    response_model=FileVersionView,
    operation_id="resources.files.versions.read",
)
def read_file_version(
    file_id: UUID, version_number: int, session: DbSession, reader: ReaderDep
) -> FileVersionView:
    version = read_version(session, file_id=file_id, version_number=version_number, reader=reader)
    return _version_view(version)


@router.get(
    "/{file_id}/versions/{version_number}/content",
    operation_id="resources.files.versions.content.read",
)
def read_file_version_content(
    file_id: UUID, version_number: int, session: DbSession, reader: ReaderDep, request: Request
) -> Response:
    """Download one version's verified bytes."""
    resource, _ = service.get_file(session, file_id=file_id, reader=reader)
    version, payload = service.read_version_content(
        session,
        file_id=file_id,
        version_number=version_number,
        reader=reader,
        storage_root=_storage_root(request),
    )
    return content_download_response(
        payload, filename=resource.name, media_type=version.media_type
    )


@router.get(
    "/{file_id}/diff",
    response_model=VersionDiffView,
    operation_id="resources.files.versions.diff",
)
def read_version_diff(
    file_id: UUID,
    session: DbSession,
    reader: ReaderDep,
    from_version: Annotated[int, Query(ge=1)],
    to_version: Annotated[int, Query(ge=1)],
) -> VersionDiffView:
    diff = version_diff(
        session,
        file_id=file_id,
        from_version=from_version,
        to_version=to_version,
        reader=reader,
    )
    return VersionDiffView(
        from_version=diff.from_version,
        to_version=diff.to_version,
        identical=diff.identical,
        from_size=diff.from_size,
        to_size=diff.to_size,
        size_delta=diff.size_delta,
    )


@router.post(
    "/{file_id}/grants",
    response_model=GrantView,
    status_code=status.HTTP_201_CREATED,
    operation_id="resources.files.grants.create",
)
def create_grant(
    file_id: UUID, payload: GrantCreateRequest, session: DbSession, reader: ReaderDep
) -> GrantView:
    with session_transaction(session):
        grant = service.grant_access(
            session,
            file_id=file_id,
            grantee_id=payload.grantee_user_id,
            permissions=list(payload.permissions),
            reader=reader,
            actor=_reader_actor(reader),
        )
    return GrantView(
        grant_id=grant.id,
        grantee_user_id=grant.grantee_user_id,
        permissions=list(grant.permissions),
        created_at=grant.created_at,
    )


@router.get(
    "/{file_id}/grants", response_model=list[GrantView], operation_id="resources.files.grants.list"
)
def list_grants(file_id: UUID, session: DbSession, reader: ReaderDep) -> list[GrantView]:
    grants = service.list_grants(session, file_id=file_id, reader=reader)
    return [
        GrantView(
            grant_id=grant.id,
            grantee_user_id=grant.grantee_user_id,
            permissions=list(grant.permissions),
            created_at=grant.created_at,
        )
        for grant in grants
    ]


@router.delete(
    "/{file_id}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="resources.files.grants.delete",
)
def delete_grant(file_id: UUID, grant_id: UUID, session: DbSession, reader: ReaderDep) -> Response:
    with session_transaction(session):
        service.revoke_access(session, file_id=file_id, grant_id=grant_id, reader=reader)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{file_id}/runtime-policy",
    response_model=RuntimePolicyView,
    operation_id="resources.files.runtime_policy.read",
)
def read_runtime_policy(file_id: UUID, session: DbSession, reader: ReaderDep) -> RuntimePolicyView:
    policy = service.get_runtime_policy(session, file_id=file_id, reader=reader)
    if policy is None:
        raise NotFoundError("The file has no runtime policy")
    return _policy_view(policy)


@router.put(
    "/{file_id}/runtime-policy",
    response_model=RuntimePolicyView,
    operation_id="resources.files.runtime_policy.update",
)
def update_runtime_policy(
    file_id: UUID, payload: RuntimePolicyRequest, session: DbSession, reader: ReaderDep
) -> RuntimePolicyView:
    with session_transaction(session):
        policy = service.set_runtime_policy(
            session,
            file_id=file_id,
            frozen_input_versions=list(payload.frozen_input_versions),
            relative_paths=list(payload.relative_paths),
            output_declarations=list(payload.output_declarations),
            use_in_execution=payload.use_in_execution,
            reader=reader,
            actor=_reader_actor(reader),
        )
    return _policy_view(policy)


def _policy_view(policy: ResourceRuntimePolicy) -> RuntimePolicyView:
    return RuntimePolicyView(
        file_id=policy.file_id,
        frozen_input_versions=list(policy.frozen_input_versions),
        relative_paths=list(policy.relative_paths),
        output_declarations=list(policy.output_declarations),
        use_in_execution=policy.use_in_execution,
        updated_at=policy.updated_at,
    )
