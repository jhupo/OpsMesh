"""Directory ownership and grants, using the current user account."""

from sqlalchemy.orm import Session

from opsmesh.resources.directories.models import (
    DIRECTORY_PERMISSIONS,
    DirectoryGrant,
    ResourceDirectory,
)
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.users import service as users
from opsmesh.users.access import CurrentUser, ensure_active_user


def require_active_reader(session: Session, actor: CurrentUser) -> CurrentUser:
    ensure_active_user(actor)
    current = users.load_identity(session, actor.user_id)
    if current is None:
        raise NotFoundError("The resource user does not exist")
    return ensure_active_user(current)


def directory_permissions(
    session: Session, *, directory: ResourceDirectory, actor: CurrentUser
) -> frozenset[str]:
    if directory.owner_user_id == actor.user_id:
        return DIRECTORY_PERMISSIONS
    grant = session.get(DirectoryGrant, (directory.id, actor.user_id), populate_existing=True)
    return frozenset(grant.permissions) if grant is not None else frozenset()


def authorize_directory(
    session: Session, *, directory: ResourceDirectory, actor: CurrentUser, permission: str
) -> frozenset[str]:
    permissions = directory_permissions(session, directory=directory, actor=actor)
    if not permissions:
        raise NotFoundError("The directory does not exist")
    if permission == "manage":
        if directory.owner_user_id != actor.user_id:
            raise ForbiddenError("Only the owner may manage this directory")
    elif permission not in DIRECTORY_PERMISSIONS:
        raise InvalidArgumentError("Unknown directory permission")
    elif permission not in permissions:
        raise ForbiddenError("The directory does not grant this operation")
    return permissions


def validate_permissions(permissions: list[str]) -> list[str]:
    selected = frozenset(permissions)
    if not selected or len(selected) != len(permissions) or not selected <= DIRECTORY_PERMISSIONS:
        raise InvalidArgumentError("Declare a nonempty unique list of directory permissions")
    if selected & {"write", "use"} and "read" not in selected:
        raise InvalidArgumentError("Write and execution use require directory read permission")
    return sorted(selected)
