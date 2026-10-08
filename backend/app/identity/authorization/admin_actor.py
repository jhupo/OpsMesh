"""Named, unrestricted administrators for attributable platform mutations."""

from fastapi import Depends

from backend.app.identity.auth.dependencies import get_current_user
from backend.app.identity.authorization.context import AuthenticatedUser
from backend.app.shared.errors import ForbiddenError

CURRENT_USER = Depends(get_current_user)


def require_admin_actor(
    user: AuthenticatedUser = CURRENT_USER,
) -> AuthenticatedUser:
    if not user.platform_admin or user.uses_restricted_token:
        raise ForbiddenError("An unrestricted platform administrator account is required")
    return user
