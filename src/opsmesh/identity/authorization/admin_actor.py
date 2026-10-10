"""Named, unrestricted administrators for attributable platform mutations."""

from fastapi import Depends

from opsmesh.identity.auth.dependencies import get_current_user
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.shared.errors import ForbiddenError

CURRENT_USER = Depends(get_current_user)


def require_admin_actor(
    user: AuthenticatedUser = CURRENT_USER,
) -> AuthenticatedUser:
    if not user.platform_admin or user.uses_restricted_token:
        raise ForbiddenError("An unrestricted platform administrator account is required")
    return user
