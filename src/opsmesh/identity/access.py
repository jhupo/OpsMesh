"""Platform-level roles and user-management authorization.

Platform modules reuse this contract; it never expresses space or project
permissions, which belong to the workspace owners.
"""

from __future__ import annotations

from enum import StrEnum

from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import UserDisabledError
from opsmesh.shared.errors import ForbiddenError


class PlatformRole(StrEnum):
    """Roles granted at platform scope only."""

    ADMIN = "admin"
    MEMBER = "member"


def ensure_active_user(user: CurrentUser) -> CurrentUser:
    """A disabled account may not act even with a valid session."""
    if user.status != "active":
        raise UserDisabledError(details={"user_id": str(user.user_id)})
    return user


def require_platform_role(user: CurrentUser, role: PlatformRole) -> CurrentUser:
    """Authorize one platform role; space roles never satisfy this check."""
    ensure_active_user(user)
    if not user.has_platform_role(role):
        raise ForbiddenError(
            "This action requires a platform role",
            details={"required_role": str(role)},
        )
    return user


def require_platform_admin(user: CurrentUser) -> CurrentUser:
    return require_platform_role(user, PlatformRole.ADMIN)
