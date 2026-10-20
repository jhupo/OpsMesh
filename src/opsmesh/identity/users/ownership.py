"""Authorize access to user-owned resources without a tenant container."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.users.models import USER_STATUS_ACTIVE, UserProfile
from opsmesh.shared.errors import ForbiddenError, NotFoundError


def require_resource_owner(
    session: Session, *, owner_user_id: UUID, user_id: UUID, lock: bool = False
) -> UserProfile:
    if owner_user_id != user_id:
        raise ForbiddenError("Only the resource owner may manage this resource")
    statement = select(UserProfile).where(UserProfile.id == owner_user_id)
    if lock:
        statement = statement.with_for_update()
    profile = session.scalar(statement.execution_options(populate_existing=True))
    if profile is None:
        raise NotFoundError("The resource owner does not exist")
    if profile.status != USER_STATUS_ACTIVE:
        raise ForbiddenError("The resource owner is disabled")
    return profile
