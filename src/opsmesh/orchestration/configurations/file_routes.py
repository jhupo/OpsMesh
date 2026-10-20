"""File operations delegated to resources after current shared-root authorization."""

from hashlib import sha256
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.orchestration.configurations.files import (
    authorize_configuration_directory,
    authorize_configuration_file,
)
from opsmesh.resources.files import service
from opsmesh.resources.files.access import ResourceReader, StorageQuotaLedger
from opsmesh.resources.files.content import content_download_response
from opsmesh.resources.files.models import ResourceFile, ResourceFileVersion
from opsmesh.resources.files.schemas import FileCreateRequest, FileVersionView, FileView
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError

router = APIRouter()


def file_view(session: DbSession, resource: ResourceFile) -> FileView:
    return FileView(
        file_id=resource.id,
        directory_id=resource.directory_id,
        name=resource.name,
        owner_user_id=resource.owner_user_id,
        latest_version=service.latest_version_number(session, file_id=resource.id),
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )


def version_view(version: ResourceFileVersion) -> FileVersionView:
    return FileVersionView(
        file_id=version.file_id,
        version=version.version,
        digest=version.digest,
        size=version.size,
        media_type=version.media_type,
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at,
    )


@router.get(
    "/{configuration_id}/files",
    response_model=list[FileView],
    operation_id="orchestration.configurations.files.list",
)
def list_files(
    configuration_id: UUID,
    directory_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
) -> list[FileView]:
    configuration, _ = authorize_configuration_directory(
        session, actor=user, configuration_id=configuration_id, directory_id=directory_id
    )
    return [
        file_view(session, resource)
        for resource in service.list_files(
            session, reader=ResourceReader(configuration.owner_user_id)
        )
        if resource.directory_id == directory_id
    ]


@router.post(
    "/{configuration_id}/files",
    response_model=FileView,
    status_code=201,
    operation_id="orchestration.configurations.files.create",
)
def create_file(
    configuration_id: UUID,
    payload: FileCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    idempotency_key: str = Query(min_length=1, max_length=150),
) -> FileView:
    with session_transaction(session):
        configuration, _ = authorize_configuration_directory(
            session,
            actor=user,
            configuration_id=configuration_id,
            directory_id=payload.directory_id,
            write=True,
        )

        def existing(identifier: UUID) -> ResourceFile:
            _, resource = authorize_configuration_file(
                session,
                actor=user,
                configuration_id=configuration_id,
                file_id=identifier,
                write=True,
            )
            return resource

        resource = run_idempotent_create(
            session=session,
            scope_id=configuration_id,
            actor_id=user.user_id,
            operation="orchestration.configurations.files.create",
            idempotency_key=idempotency_key,
            request_payload=payload.model_dump(mode="json"),
            get_existing=existing,
            create=lambda: service.create_file(
                session,
                owner_id=configuration.owner_user_id,
                directory_id=payload.directory_id,
                name=payload.name,
                reader=ResourceReader(configuration.owner_user_id),
            ),
            resource_id=lambda record: record.id,
        )
    return file_view(session, resource)


@router.get(
    "/{configuration_id}/files/{file_id}/versions/{number}/content",
    operation_id="orchestration.configurations.files.content",
)
def read_content(
    configuration_id: UUID,
    file_id: UUID,
    number: int,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> Response:
    configuration, resource = authorize_configuration_file(
        session, actor=user, configuration_id=configuration_id, file_id=file_id
    )
    version, payload = service.read_version_content(
        session,
        file_id=file_id,
        version_number=number,
        reader=ResourceReader(configuration.owner_user_id),
        storage_root=Path(request.app.state.storage_root),
    )
    return content_download_response(payload, filename=resource.name, media_type=version.media_type)


@router.put(
    "/{configuration_id}/files/{file_id}/content",
    response_model=FileVersionView,
    operation_id="orchestration.configurations.files.write",
)
def write_content(
    configuration_id: UUID,
    file_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    content: bytes = Body(media_type="application/octet-stream"),
    expected_version: int = Query(ge=0),
    idempotency_key: str = Query(min_length=1, max_length=150),
) -> FileVersionView:
    ledger = getattr(request.app.state, "storage_quota_ledger", None)
    if not isinstance(ledger, StorageQuotaLedger):
        raise HTTPException(503, "Storage quota accounting is not assembled")
    with session_transaction(session):
        configuration, _ = authorize_configuration_file(
            session, actor=user, configuration_id=configuration_id, file_id=file_id, write=True
        )

        def existing(identifier: UUID) -> ResourceFileVersion:
            record = session.get(ResourceFileVersion, identifier)
            if record is None or record.file_id != file_id:
                raise NotFoundError("The file write receipt is unavailable")
            return record

        record = run_idempotent_create(
            session=session,
            scope_id=configuration_id,
            actor_id=user.user_id,
            operation=f"orchestration.configurations.files.write:{file_id}",
            idempotency_key=idempotency_key,
            request_payload={
                "expected_version": expected_version,
                "size": len(content),
                "content_digest": sha256(content).hexdigest(),
            },
            get_existing=existing,
            create=lambda: service.write_back(
                session,
                file_id=file_id,
                expected_version=expected_version,
                content=content,
                media_type=request.headers.get("content-type"),
                reader=ResourceReader(configuration.owner_user_id),
                actor=AuditActor(actor_type="user", actor_id=str(user.user_id)),
                ledger=ledger,
                storage_root=Path(request.app.state.storage_root),
                idempotency_key=f"configuration:{configuration_id}:{user.user_id}:{idempotency_key}",
            ),
            resource_id=lambda version: version.id,
        )
    return version_view(record)
