"""Persistent roots, directory grants and authorized client file operations.

Registration is transactional; storage is materialized idempotently on first use.
Unregistering revokes future access but retains contents. Runtime owns mounts and
must release sandbox access before physical storage can be collected.
"""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select, text
from sqlalchemy.orm import Session

from opsmesh.resources.directories.access import (
    authorize_directory,
    require_active_reader,
    validate_permissions,
)
from opsmesh.resources.directories.models import (
    DIRECTORY_ACTIVE,
    DIRECTORY_DELETED,
    DirectoryGrant,
    ResourceDirectory,
)
from opsmesh.resources.storage.base import FileStorage, LocalDirectoryStorage
from opsmesh.resources.storage.contracts import FileEntry
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.users import service as users
from opsmesh.users.access import CurrentUser, ensure_active_user


@dataclass(frozen=True)
class DirectoryMount:
    """Internal mount input; never expose host paths in API responses."""

    directory_id: UUID
    host_path: Path = field(repr=False)
    read_only: bool


def _name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 200 or any(ord(char) < 32 for char in name):
        raise InvalidArgumentError("A directory name must contain 1 to 200 printable characters")
    return name


def create_directory(
    session: Session, *, actor: CurrentUser, name: str = "Working directory"
) -> ResourceDirectory:
    require_active_reader(session, actor)
    directory_id = uuid4()
    directory = ResourceDirectory(
        id=directory_id,
        owner_user_id=actor.user_id,
        name=_name(name),
        storage_key=f"{actor.user_id.hex}/{directory_id.hex}",
        status=DIRECTORY_ACTIVE,
        is_default=False,
    )
    session.add(directory)
    session.flush()
    return directory


def ensure_default_directory(session: Session, *, actor: CurrentUser) -> ResourceDirectory:
    require_active_reader(session, actor)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"default-resource-directory:{actor.user_id}"},
    )
    directory = session.scalar(
        select(ResourceDirectory)
        .where(
            ResourceDirectory.owner_user_id == actor.user_id,
            ResourceDirectory.is_default.is_(True),
            ResourceDirectory.status == DIRECTORY_ACTIVE,
        )
        .execution_options(populate_existing=True)
    )
    if directory is None:
        directory = create_directory(session, actor=actor)
        directory.is_default = True
        session.flush()
    return directory


def list_directories(
    session: Session, *, actor: CurrentUser, page: PageParams
) -> tuple[list[ResourceDirectory], int]:
    require_active_reader(session, actor)
    granted = exists().where(
        DirectoryGrant.directory_id == ResourceDirectory.id,
        DirectoryGrant.grantee_user_id == actor.user_id,
    )
    return page_scalars(
        session,
        select(ResourceDirectory)
        .where(
            ResourceDirectory.status == DIRECTORY_ACTIVE,
            or_(ResourceDirectory.owner_user_id == actor.user_id, granted),
        )
        .order_by(ResourceDirectory.created_at, ResourceDirectory.id)
        .execution_options(populate_existing=True),
        page,
    )


def get_directory(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    permission: str = "read",
    lock: bool = False,
) -> ResourceDirectory:
    require_active_reader(session, actor)
    statement = select(ResourceDirectory).where(
        ResourceDirectory.id == directory_id, ResourceDirectory.status == DIRECTORY_ACTIVE
    )
    if lock:
        statement = statement.with_for_update()
    directory = session.scalar(statement.execution_options(populate_existing=True))
    if directory is None:
        raise NotFoundError("The directory does not exist")
    authorize_directory(session, directory=directory, actor=actor, permission=permission)
    return directory


def rename_directory(
    session: Session, *, actor: CurrentUser, directory_id: UUID, name: str
) -> ResourceDirectory:
    directory = get_directory(
        session, actor=actor, directory_id=directory_id, permission="manage", lock=True
    )
    directory.name = _name(name)
    session.flush()
    return directory


def delete_directory(session: Session, *, actor: CurrentUser, directory_id: UUID) -> None:
    """Unregister a root and revoke grants, retaining its persistent contents."""
    require_active_reader(session, actor)
    directory = session.scalar(
        select(ResourceDirectory)
        .where(ResourceDirectory.id == directory_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if directory is None:
        raise NotFoundError("The directory does not exist")
    authorize_directory(session, directory=directory, actor=actor, permission="manage")
    directory.status = DIRECTORY_DELETED
    for grant in session.scalars(
        select(DirectoryGrant).where(DirectoryGrant.directory_id == directory_id)
    ):
        session.delete(grant)
    session.flush()


def list_grants(
    session: Session, *, actor: CurrentUser, directory_id: UUID, page: PageParams
) -> tuple[list[DirectoryGrant], int]:
    get_directory(session, actor=actor, directory_id=directory_id, permission="manage")
    return page_scalars(
        session,
        select(DirectoryGrant)
        .where(DirectoryGrant.directory_id == directory_id)
        .order_by(DirectoryGrant.grantee_user_id)
        .execution_options(populate_existing=True),
        page,
    )


def set_grant(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    grantee_user_id: UUID,
    permissions: list[str],
) -> DirectoryGrant:
    selected = validate_permissions(permissions)
    directory = get_directory(
        session, actor=actor, directory_id=directory_id, permission="manage", lock=True
    )
    if grantee_user_id == directory.owner_user_id:
        raise InvalidArgumentError("The directory owner already has all permissions")
    grantee = users.load_identity(session, grantee_user_id)
    if grantee is None:
        raise NotFoundError("The recipient user does not exist")
    ensure_active_user(grantee)
    grant = session.get(DirectoryGrant, (directory_id, grantee_user_id), populate_existing=True)
    if grant is None:
        grant = DirectoryGrant(directory_id=directory_id, grantee_user_id=grantee_user_id)
        session.add(grant)
    grant.permissions = selected
    session.flush()
    return grant


def revoke_grant(
    session: Session, *, actor: CurrentUser, directory_id: UUID, grantee_user_id: UUID
) -> None:
    get_directory(session, actor=actor, directory_id=directory_id, permission="manage", lock=True)
    grant = session.get(DirectoryGrant, (directory_id, grantee_user_id), populate_existing=True)
    if grant is not None:
        session.delete(grant)
        session.flush()


def resolve_directory(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    filesystem: LocalDirectoryStorage,
    writable: bool = False,
) -> DirectoryMount:
    """Resolve an authorized mount; runtime owns mounting and subsequent reauthorization."""
    directory = get_directory(session, actor=actor, directory_id=directory_id, permission="use")
    if writable:
        authorize_directory(session, directory=directory, actor=actor, permission="write")
    return DirectoryMount(
        directory_id=directory.id,
        host_path=filesystem.local_directory(directory.storage_key),
        read_only=not writable,
    )


def list_files(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    filesystem: FileStorage,
    path: str,
    page: PageParams,
) -> tuple[list[FileEntry], int]:
    directory = get_directory(session, actor=actor, directory_id=directory_id)
    filesystem.ensure_directory(directory.storage_key)
    return filesystem.list_entries(directory.storage_key, path=path, page=page)


def read_file(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    filesystem: FileStorage,
    path: str,
) -> bytes:
    directory = get_directory(session, actor=actor, directory_id=directory_id)
    filesystem.ensure_directory(directory.storage_key)
    return filesystem.read_file(directory.storage_key, path=path)


def write_file(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    filesystem: FileStorage,
    path: str,
    content: bytes,
) -> None:
    directory = get_directory(
        session, actor=actor, directory_id=directory_id, permission="write", lock=True
    )
    filesystem.ensure_directory(directory.storage_key)
    filesystem.write_file(directory.storage_key, path=path, content=content)


def create_subdirectory(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    filesystem: FileStorage,
    path: str,
) -> None:
    directory = get_directory(
        session, actor=actor, directory_id=directory_id, permission="write", lock=True
    )
    filesystem.ensure_directory(directory.storage_key)
    filesystem.create_subdirectory(directory.storage_key, path=path)


def delete_entry(
    session: Session,
    *,
    actor: CurrentUser,
    directory_id: UUID,
    filesystem: FileStorage,
    path: str,
) -> None:
    directory = get_directory(
        session, actor=actor, directory_id=directory_id, permission="write", lock=True
    )
    filesystem.delete_entry(directory.storage_key, path=path)
