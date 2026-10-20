"""A binding never grants visibility of an otherwise private definition."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.capabilities.lifecycle import Viewer, authorize_version_read
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.ownership import require_resource_owner
from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
from opsmesh.shared.errors import ForbiddenError, NotFoundError


def load_binding(
    session: Session,
    *,
    actor: CurrentUser,
    owner_user_id: UUID,
    binding_id: UUID,
    use: bool = False,
    lock: bool = False,
) -> tuple[CapabilityBinding, CapabilityVersion, str]:
    require_resource_owner(session, owner_user_id=owner_user_id, user_id=actor.user_id)
    binding = session.get(
        CapabilityBinding, binding_id, with_for_update=lock, populate_existing=True
    )
    if binding is None or binding.owner_user_id != owner_user_id:
        raise NotFoundError("Capability binding not found")
    version = session.get(CapabilityVersion, binding.version_id)
    if version is None or version.status != "published" or version.digest != binding.version_digest:
        raise ForbiddenError("The binding version is unavailable")
    definition = authorize_version_read(
        session,
        version=version,
        viewer=Viewer(user_id=actor.user_id, is_platform_admin=False),
    )
    if use and (binding.status != "enabled" or definition.status != "active"):
        raise ForbiddenError("The binding or capability is disabled")
    return binding, version, definition.type
