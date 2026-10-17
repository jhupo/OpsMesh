"""File operations: state, authorization, CAS write-back and quota coordination.

Every content upload reserves storage on the injected identity ledger,
writes the blob, records the version and settles the reservation — all
inside the caller's transaction. A failure anywhere rolls the database
back and the session events delete the staged blob, so no orphan content
and no phantom reservation survive. A write-back is compare-and-set on
the current version: a stale expectation is a conflict, never a silent
overwrite.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.resources.files.access import (
    ResourceReader,
    ScopeAdmission,
    ScopeRef,
    StorageQuotaLedger,
    authorize_delete,
    authorize_manage,
    authorize_read,
    authorize_write,
    validate_permissions,
)
from opsmesh.resources.files.models import (
    ResourceDirectory,
    ResourceFile,
    ResourceFileContent,
    ResourceFileGrant,
    ResourceFileVersion,
    ResourceRuntimePolicy,
)
from opsmesh.resources.files.schemas import OutputDeclaration
from opsmesh.resources.storage.filesystem import (
    MAX_CONTENT_BYTES,
    content_digest,
    new_content_id,
    read_content,
    write_content,
)
from opsmesh.resources.storage.transactions import stage_content, stage_content_removal
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.files import validate_relative_path

STORAGE_IDEMPOTENCY_SCOPE = "resource-file-content"


def create_directory(
    session: Session,
    *,
    owner_id: UUID,
    path: str,
    admission: ScopeAdmission,
    reader: ResourceReader,
    scope: ScopeRef,
) -> ResourceDirectory:
    """Create one user directory at a validated relative path."""
    admission.admit(session, reader=reader, scope=scope)
    try:
        validated = validate_relative_path(path)
    except ValueError as exc:
        raise InvalidArgumentError("The directory path is not a safe relative path") from exc
    existing = session.scalar(
        select(ResourceDirectory).where(
            ResourceDirectory.owner_user_id == owner_id,
            ResourceDirectory.path == validated,
        )
    )
    if existing is not None:
        raise ConflictError("The directory already exists", details={"path": validated})
    directory = ResourceDirectory(owner_user_id=owner_id, path=validated)
    session.add(directory)
    session.flush()
    return directory


def list_directories(session: Session, *, owner_id: UUID) -> list[ResourceDirectory]:
    return list(
        session.scalars(
            select(ResourceDirectory)
            .where(ResourceDirectory.owner_user_id == owner_id)
            .order_by(ResourceDirectory.path.asc())
        )
    )


def delete_directory(
    session: Session,
    *,
    directory_id: UUID,
    reader: ResourceReader,
) -> None:
    """Delete one empty directory; a directory with files is refused."""
    directory = session.get(ResourceDirectory, directory_id)
    if directory is None or directory.owner_user_id != reader.user_id:
        raise NotFoundError("The directory does not exist")
    child = session.scalar(
        select(ResourceFile.id)
        .where(ResourceFile.directory_id == directory_id)
        .limit(1)
    )
    if child is not None:
        raise ConflictError("The directory still contains files")
    child_directory = session.scalar(
        select(ResourceDirectory.id)
        .where(ResourceDirectory.parent_id == directory_id)
        .limit(1)
    )
    if child_directory is not None:
        raise ConflictError("The directory still contains directories")
    session.delete(directory)
    session.flush()


def create_file(
    session: Session,
    *,
    owner_id: UUID,
    directory_id: UUID,
    name: str,
    reader: ResourceReader,
) -> ResourceFile:
    """Create one empty file; its first version arrives with content."""
    directory = session.get(ResourceDirectory, directory_id)
    if directory is None or directory.owner_user_id != owner_id:
        raise NotFoundError("The directory does not exist")
    if not name.strip() or "/" in name or "\\" in name or name in {".", ".."}:
        raise InvalidArgumentError("The file name is not a single safe path segment")
    existing = session.scalar(
        select(ResourceFile).where(
            ResourceFile.owner_user_id == owner_id,
            ResourceFile.directory_id == directory_id,
            ResourceFile.name == name,
        )
    )
    if existing is not None:
        raise ConflictError(
            "The file already exists", details={"name": name}
        )
    resource = ResourceFile(
        owner_user_id=owner_id, directory_id=directory_id, name=name
    )
    session.add(resource)
    session.flush()
    return resource


def list_files(session: Session, *, reader: ResourceReader) -> list[ResourceFile]:
    """The files the reader owns or holds a grant on."""
    granted = select(ResourceFileGrant.file_id).where(
        ResourceFileGrant.grantee_user_id == reader.user_id
    )
    return list(
        session.scalars(
            select(ResourceFile)
            .where(
                (ResourceFile.owner_user_id == reader.user_id)
                | ResourceFile.id.in_(granted)
            )
            .order_by(ResourceFile.created_at.desc())
        )
    )


def get_file(
    session: Session, *, file_id: UUID, reader: ResourceReader
) -> tuple[ResourceFile, ResourceFileGrant | None]:
    """One file the reader may see, with the grant that admits it."""
    resource = session.get(ResourceFile, file_id)
    if resource is None:
        raise NotFoundError("The file does not exist")
    grant = None
    if resource.owner_user_id != reader.user_id:
        grant = session.scalar(
            select(ResourceFileGrant).where(
                ResourceFileGrant.file_id == file_id,
                ResourceFileGrant.grantee_user_id == reader.user_id,
            )
        )
        authorize_read(resource, reader=reader, grant=grant)
    return resource, grant


def _lock_file_row(session: Session, *, file_id: UUID) -> None:
    """Serialize every version mutation of one file on its row.

    A version number is allocated from the committed state and a
    compare-and-set expectation is checked against it, so two concurrent
    writers must not both read the same pre-state: PostgreSQL row locks
    give that ordering inside one transaction, and the loser observes the
    winner's committed version instead of a unique constraint error.
    """
    session.execute(
        select(ResourceFile.id).where(ResourceFile.id == file_id).with_for_update()
    )


def upload_version(
    session: Session,
    *,
    file_id: UUID,
    content: bytes,
    media_type: str | None,
    reader: ResourceReader,
    actor: AuditActor,
    ledger: StorageQuotaLedger,
    storage_root: Path,
    idempotency_key: str | None,
) -> ResourceFileVersion:
    """Store one new immutable version of a file.

    The storage reservation is taken on the owner's ledger before the blob
    is written and settled with the actual bytes after the version row
    exists; any failure rolls the whole transaction back and the session
    events delete the staged blob. A repeated idempotency key replays the
    receipt instead of storing a second version.
    """
    resource, grant = get_file(session, file_id=file_id, reader=reader)
    authorize_write(resource, reader=reader, grant=grant)
    if not content:
        raise InvalidArgumentError("The file content must not be empty")
    if len(content) > MAX_CONTENT_BYTES:
        raise InvalidArgumentError("The content exceeds the permitted size")
    digest = content_digest(content)
    _lock_file_row(session, file_id=resource.id)
    return run_idempotent_create(
        session=session,
        scope_id=resource.owner_user_id,
        actor_id=reader.user_id,
        operation="resource.file.version",
        idempotency_key=idempotency_key,
        request_payload={
            "file_id": str(file_id),
            "digest": digest,
            "size": len(content),
            "media_type": media_type,
        },
        get_existing=lambda version_id: _replayed_version(
            session, version_id=version_id, reader=reader
        ),
        create=lambda: _store_version(
            session,
            resource=resource,
            content=content,
            digest=digest,
            media_type=media_type,
            reader=reader,
            actor=actor,
            ledger=ledger,
            storage_root=storage_root,
            reservation_key=idempotency_key,
        ),
        resource_id=lambda version: version.id,
    )


def _store_version(
    session: Session,
    *,
    resource: ResourceFile,
    content: bytes,
    digest: str,
    media_type: str | None,
    reader: ResourceReader,
    actor: AuditActor,
    ledger: StorageQuotaLedger,
    storage_root: Path,
    reservation_key: str | None,
) -> ResourceFileVersion:
    """Reserve, write, record and settle one version inside the transaction."""
    reservation_id = ledger.reserve_storage(
        session,
        user_id=resource.owner_user_id,
        amount=len(content),
        idempotency_scope=STORAGE_IDEMPOTENCY_SCOPE,
        idempotency_key=reservation_key or f"auto:{uuid4()}",
    )
    content_id = new_content_id()
    stored_digest = write_content(storage_root, content_id=content_id, payload=content)
    stage_content(session, storage_root, content_id)
    session.add(
        ResourceFileContent(
            id=content_id,
            digest=stored_digest,
            size=len(content),
            storage_key=str(content_id),
        )
    )
    version = ResourceFileVersion(
        file_id=resource.id,
        version=_next_version_number(session, file_id=resource.id),
        content_id=content_id,
        digest=stored_digest,
        size=len(content),
        media_type=media_type,
        created_by_user_id=reader.user_id,
    )
    session.add(version)
    session.flush()
    ledger.settle_storage(
        session, reservation_id=reservation_id, actual_amount=len(content)
    )
    append_platform_event(
        session,
        actor=actor,
        action="resource.file_version_created",
        target_type="resource_file",
        target_id=str(resource.id),
        summary=f"Stored version {version.version} of a file",
        details={"version": version.version, "size": len(content), "digest": stored_digest},
    )
    return version


def write_back(
    session: Session,
    *,
    file_id: UUID,
    expected_version: int,
    content: bytes,
    media_type: str | None,
    reader: ResourceReader,
    actor: AuditActor,
    ledger: StorageQuotaLedger,
    storage_root: Path,
    idempotency_key: str | None,
) -> ResourceFileVersion:
    """Replace the file content under a compare-and-set expectation.

    A writer that read version N must send N back; a different current
    version is a conflict rather than a silent overwrite of someone
    else's change. The expectation is checked under the file row lock, so
    a concurrent write-back cannot pass the same check from a stale read.
    """
    resource, grant = get_file(session, file_id=file_id, reader=reader)
    authorize_write(resource, reader=reader, grant=grant)
    _lock_file_row(session, file_id=resource.id)
    current = latest_version_number(session, file_id=resource.id)
    if current != expected_version:
        raise ConflictError(
            "The file changed since the expected version",
            details={"expected_version": expected_version, "current_version": current},
        )
    return upload_version(
        session,
        file_id=file_id,
        content=content,
        media_type=media_type,
        reader=reader,
        actor=actor,
        ledger=ledger,
        storage_root=storage_root,
        idempotency_key=idempotency_key,
    )


def delete_file(
    session: Session,
    *,
    file_id: UUID,
    reader: ResourceReader,
    storage_root: Path,
) -> None:
    """Delete one file with its versions, grants, policy and content blobs.

    The rows are removed with explicit statements in dependency order — the
    ORM's flush ordering across tables without relationships is not a
    contract this deletion may rely on. The file row is locked first, so a
    concurrent upload cannot interleave a version insert with the delete.
    """
    resource, grant = get_file(session, file_id=file_id, reader=reader)
    authorize_delete(resource, reader=reader, grant=grant)
    _lock_file_row(session, file_id=resource.id)
    content_ids = list(
        session.scalars(
            select(ResourceFileContent.id)
            .join(
                ResourceFileVersion,
                ResourceFileVersion.content_id == ResourceFileContent.id,
            )
            .where(ResourceFileVersion.file_id == resource.id)
        )
    )
    session.execute(
        delete(ResourceFileVersion).where(ResourceFileVersion.file_id == resource.id)
    )
    session.execute(delete(ResourceFileGrant).where(ResourceFileGrant.file_id == resource.id))
    session.execute(
        delete(ResourceRuntimePolicy).where(ResourceRuntimePolicy.file_id == resource.id)
    )
    session.execute(delete(ResourceFile).where(ResourceFile.id == resource.id))
    session.execute(
        delete(ResourceFileContent).where(ResourceFileContent.id.in_(content_ids))
    )
    session.flush()
    for content_id in content_ids:
        # The blob leaves the storage root only once the deletion commits;
        # a rolled-back deletion keeps content the database still records.
        stage_content_removal(session, storage_root, content_id)


def _replayed_version(
    session: Session, *, version_id: UUID, reader: ResourceReader
) -> ResourceFileVersion | None:
    """A replayed upload still requires current read access to the file."""
    version = session.get(ResourceFileVersion, version_id)
    if version is None:
        return None
    resource, grant = get_file(session, file_id=version.file_id, reader=reader)
    authorize_read(resource, reader=reader, grant=grant)
    return version


def grant_access(
    session: Session,
    *,
    file_id: UUID,
    grantee_id: UUID,
    permissions: list[str],
    reader: ResourceReader,
    actor: AuditActor,
) -> ResourceFileGrant:
    """Grant one user explicit permissions on one file."""
    resource, _ = get_file(session, file_id=file_id, reader=reader)
    authorize_manage(resource, reader=reader)
    validated = validate_permissions(permissions)
    if not validated:
        raise InvalidArgumentError("A grant must declare at least one permission")
    if grantee_id == resource.owner_user_id:
        raise InvalidArgumentError("The owner already holds every permission")
    existing = session.scalar(
        select(ResourceFileGrant).where(
            ResourceFileGrant.file_id == resource.id,
            ResourceFileGrant.grantee_user_id == grantee_id,
        )
    )
    if existing is not None:
        existing.permissions = validated
        grant = existing
    else:
        grant = ResourceFileGrant(
            file_id=resource.id,
            grantee_user_id=grantee_id,
            permissions=validated,
            created_by_user_id=reader.user_id,
        )
        session.add(grant)
    session.flush()
    append_platform_event(
        session,
        actor=actor,
        action="resource.file_granted",
        target_type="resource_file",
        target_id=str(resource.id),
        summary="Granted file permissions",
        details={"grantee_user_id": str(grantee_id), "permissions": validated},
    )
    return grant


def revoke_access(
    session: Session, *, file_id: UUID, grant_id: UUID, reader: ResourceReader
) -> None:
    """Revoke one grant; the owner keeps every permission."""
    resource, _ = get_file(session, file_id=file_id, reader=reader)
    authorize_manage(resource, reader=reader)
    grant = session.get(ResourceFileGrant, grant_id)
    if grant is None or grant.file_id != resource.id:
        raise NotFoundError("The grant does not exist")
    session.delete(grant)
    session.flush()


def list_grants(
    session: Session, *, file_id: UUID, reader: ResourceReader
) -> list[ResourceFileGrant]:
    resource, _ = get_file(session, file_id=file_id, reader=reader)
    authorize_manage(resource, reader=reader)
    return list(
        session.scalars(
            select(ResourceFileGrant)
            .where(ResourceFileGrant.file_id == resource.id)
            .order_by(ResourceFileGrant.created_at.asc())
        )
    )


def _validated_output_declarations(
    session: Session,
    *,
    resource: ResourceFile,
    declarations: Sequence[OutputDeclaration | Mapping[str, object]],
) -> list[dict[str, object]]:
    """Validate every output declaration and normalize it for storage.

    The request schema already rejects unknown fields and bounds lengths,
    but the service is the gate every caller passes: each declaration is
    re-validated here and its path checked as a safe relative path again,
    because a persisted declaration is later resolved against a storage
    root. A path another file of the same owner already occupies is
    refused so a write-back through this policy cannot bypass that file's
    own grants.
    """
    validated: list[dict[str, object]] = []
    for declaration in declarations:
        if isinstance(declaration, OutputDeclaration):
            model = declaration
        else:
            try:
                model = OutputDeclaration.model_validate(declaration)
            except ValidationError as exc:
                raise InvalidArgumentError(
                    "An output declaration violates its contract",
                    details={"reason": str(exc)},
                ) from exc
        try:
            path = validate_relative_path(model.path)
        except ValueError as exc:
            raise InvalidArgumentError(
                "An output declaration path is not a safe relative path"
            ) from exc
        _reject_declared_file_collision(session, resource=resource, path=path)
        validated.append(model.model_dump(mode="json"))
    return validated


def _reject_declared_file_collision(
    session: Session, *, resource: ResourceFile, path: str
) -> None:
    """Refuse an output declaration whose resolved path another file occupies.

    Output paths resolve inside the owning file's directory; if another
    file of the same owner lives at the resolved path, a run writing back
    through this policy would overwrite that file without holding its
    grants.
    """
    parent, _, name = path.rpartition("/")
    directory_path = session.scalar(
        select(ResourceDirectory.path).where(ResourceDirectory.id == resource.directory_id)
    )
    target_directory = f"{directory_path}/{parent}" if parent else directory_path
    occupied = session.scalar(
        select(ResourceFile.id)
        .join(ResourceDirectory, ResourceFile.directory_id == ResourceDirectory.id)
        .where(
            ResourceDirectory.owner_user_id == resource.owner_user_id,
            ResourceDirectory.path == target_directory,
            ResourceFile.name == name,
            ResourceFile.id != resource.id,
        )
        .limit(1)
    )
    if occupied is not None:
        raise ConflictError(
            "An output declaration claims a path another file occupies",
            details={"path": path},
        )


def set_runtime_policy(
    session: Session,
    *,
    file_id: UUID,
    frozen_input_versions: list[int],
    relative_paths: list[str],
    output_declarations: Sequence[OutputDeclaration | Mapping[str, object]],
    use_in_execution: bool,
    reader: ResourceReader,
    actor: AuditActor,
) -> ResourceRuntimePolicy:
    """Record what execution may consume from this file and write back."""
    resource, _ = get_file(session, file_id=file_id, reader=reader)
    authorize_manage(resource, reader=reader)
    validated_paths = []
    for path in relative_paths:
        try:
            validated_paths.append(validate_relative_path(path))
        except ValueError as exc:
            raise InvalidArgumentError(
                "A runtime relative path is not a safe relative path"
            ) from exc
    declarations = _validated_output_declarations(
        session, resource=resource, declarations=output_declarations
    )
    frozen = []
    for number in frozen_input_versions:
        version = session.scalar(
            select(ResourceFileVersion.id).where(
                ResourceFileVersion.file_id == resource.id,
                ResourceFileVersion.version == number,
            )
        )
        if version is None:
            raise InvalidArgumentError(
                "A frozen input version does not exist", details={"version": number}
            )
        frozen.append(number)
    policy = session.scalar(
        select(ResourceRuntimePolicy).where(ResourceRuntimePolicy.file_id == resource.id)
    )
    if policy is None:
        policy = ResourceRuntimePolicy(
            file_id=resource.id,
            frozen_input_versions=frozen,
            relative_paths=validated_paths,
            output_declarations=declarations,
            use_in_execution=use_in_execution,
            updated_by_user_id=reader.user_id,
        )
        session.add(policy)
    else:
        policy.frozen_input_versions = frozen
        policy.relative_paths = validated_paths
        policy.output_declarations = declarations
        policy.use_in_execution = use_in_execution
        policy.updated_by_user_id = reader.user_id
    session.flush()
    append_platform_event(
        session,
        actor=actor,
        action="resource.runtime_policy_updated",
        target_type="resource_file",
        target_id=str(resource.id),
        summary="Updated the file runtime policy",
        details={"use_in_execution": use_in_execution, "frozen_versions": frozen},
    )
    return policy


def get_runtime_policy(
    session: Session, *, file_id: UUID, reader: ResourceReader
) -> ResourceRuntimePolicy | None:
    resource, _ = get_file(session, file_id=file_id, reader=reader)
    authorize_manage(resource, reader=reader)
    return session.scalar(
        select(ResourceRuntimePolicy).where(ResourceRuntimePolicy.file_id == resource.id)
    )


def read_version_content(
    session: Session,
    *,
    file_id: UUID,
    version_number: int,
    reader: ResourceReader,
    storage_root: Path,
) -> tuple[ResourceFileVersion, bytes]:
    """Read one version's content, verifying the blob against its digest."""
    resource, grant = get_file(session, file_id=file_id, reader=reader)
    authorize_read(resource, reader=reader, grant=grant)
    version = session.scalar(
        select(ResourceFileVersion).where(
            ResourceFileVersion.file_id == resource.id,
            ResourceFileVersion.version == version_number,
        )
    )
    if version is None:
        raise NotFoundError("The file version does not exist")
    payload = read_content(
        storage_root, content_id=version.content_id, expected_digest=version.digest
    )
    return version, payload


def latest_version_number(session: Session, *, file_id: UUID) -> int:
    """The current version number of one file; zero before its first upload."""
    latest = session.scalar(
        select(ResourceFileVersion.version)
        .where(ResourceFileVersion.file_id == file_id)
        .order_by(ResourceFileVersion.version.desc())
        .limit(1)
    )
    return int(latest or 0)


def _next_version_number(session: Session, *, file_id: UUID) -> int:
    return latest_version_number(session, file_id=file_id) + 1


__all__ = [
    "create_directory",
    "create_file",
    "delete_directory",
    "delete_file",
    "get_file",
    "get_runtime_policy",
    "grant_access",
    "latest_version_number",
    "list_directories",
    "list_files",
    "list_grants",
    "read_version_content",
    "revoke_access",
    "set_runtime_policy",
    "upload_version",
    "write_back",
]
