"""Current extension authorization; identifiers never establish a principal."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.capabilities.lifecycle import Viewer, authorize_version_read
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.extensions.models import ExtensionInstance, Installation, Invocation
from opsmesh.extensions.security import authorize_permissions
from opsmesh.identity.auth.contracts import UserDirectory
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError


class ScopeAuthorization(Protocol):
    def require_admission(self, session: Session) -> None: ...

    def admit_instance(
        self,
        session: Session,
        *,
        user_id: UUID,
        owner_user_id: UUID,
        instance_id: UUID,
        configuration_id: UUID,
    ) -> None: ...

    def admit_source(
        self,
        session: Session,
        *,
        source_run_id: UUID | None,
        principal_id: UUID,
        configuration_id: UUID | None,
    ) -> None: ...

    def admit(
        self,
        session: Session,
        *,
        user_id: UUID,
        configuration_id: UUID | None,
        execution: bool = False,
    ) -> None: ...


def require_admin(user: CurrentUser) -> None:
    if user.status != "active" or not user.has_platform_role("admin"):
        raise ForbiddenError("Only a current administrator may manage installations")


def load_installation(
    session: Session, installation_id: UUID, *, lock: bool = False
) -> Installation:
    installation = session.get(Installation, installation_id, with_for_update=lock)
    if installation is None:
        raise NotFoundError("Extension installation not found")
    return installation


def authorize_instance(
    session: Session,
    instance_id: UUID,
    *,
    user: CurrentUser,
    scopes: ScopeAuthorization,
    lock: bool = False,
    execution: bool = False,
) -> ExtensionInstance:
    instance = session.get(ExtensionInstance, instance_id)
    if instance is None:
        raise NotFoundError("Extension instance not found")
    if instance.owner_id != user.user_id:
        if not execution or instance.configuration_id is None:
            raise NotFoundError("Extension instance not found")
        scopes.admit_instance(
            session,
            user_id=user.user_id,
            owner_user_id=instance.owner_id,
            instance_id=instance.id,
            configuration_id=instance.configuration_id,
        )
    if user.status != "active":
        raise ForbiddenError("The invocation principal is not active")
    scopes.admit(
        session,
        user_id=user.user_id,
        configuration_id=instance.configuration_id,
        execution=execution,
    )
    if lock:
        scope = (instance.owner_id, instance.configuration_id)
        instance = session.get(
            ExtensionInstance, instance_id, with_for_update=True, populate_existing=True
        )
        if instance is None or scope != (
            instance.owner_id,
            instance.configuration_id,
        ):
            raise ConflictError("The instance scope changed during authorization")
    return instance


def installation_viewer(session: Session, instance: ExtensionInstance) -> CurrentUser:
    """A configured export keeps its publisher's visibility grant, never their identity."""
    owner = UserIdentityDirectory().load_identity(session, instance.owner_id)
    if owner is None or owner.status != "active":
        raise ForbiddenError("The instance owner is unavailable")
    return owner


def authorize_invocation(
    session: Session,
    invocation: Invocation,
    *,
    users: UserDirectory,
    scopes: ScopeAuthorization,
    permission: str | None = None,
) -> tuple[CurrentUser, ExtensionInstance, Installation]:
    user = users.load_identity(session, invocation.principal_id)
    if user is None:
        raise ForbiddenError("The invocation principal no longer exists")
    instance = authorize_instance(
        session, invocation.instance_id, user=user, scopes=scopes, execution=True
    )
    source = invocation.snapshot.get("run_id")
    scopes.admit_source(
        session,
        source_run_id=UUID(str(source)) if source else None,
        principal_id=invocation.principal_id,
        configuration_id=instance.configuration_id,
    )
    installation = load_installation(session, invocation.installation_id)
    authorize_installation(session, installation, user=installation_viewer(session, instance))
    if installation.status != "enabled" or instance.status != "enabled":
        raise ForbiddenError("The installation or instance is disabled")
    if invocation.status not in {"queued", "started"} or invocation.cancel_requested:
        raise ConflictError("The invocation is not active")
    if invocation.deadline <= datetime.now(UTC):
        raise ForbiddenError("The invocation deadline has expired")
    # Configuration revision changes revoke outstanding delegation. The frozen
    # artifact is never silently redirected to the instance's replacement.
    if instance.revision != invocation.instance_revision:
        raise ForbiddenError("The instance authorization revision changed")
    authorize_permissions(
        ceiling=installation.permission_ceiling,
        instance=instance.permissions,
        requested=invocation.snapshot.get("permissions", []),
        permission=permission,
    )
    return user, instance, installation


def authorize_installation(
    session: Session, installation: Installation, *, user: CurrentUser
) -> None:
    version = session.get(CapabilityVersion, installation.version_id)
    if version is None:
        raise NotFoundError("The installed capability version is unavailable")
    definition = authorize_version_read(
        session,
        version=version,
        viewer=Viewer(user_id=user.user_id, is_platform_admin=user.has_platform_role("admin")),
    )
    if definition.status != "active":
        raise ForbiddenError("The installed capability is not active")
