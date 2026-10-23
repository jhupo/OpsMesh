"""Verified user identity and account role checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from opsmesh.shared.errors import ForbiddenError
from opsmesh.users.errors import UserDisabledError


@dataclass(frozen=True)
class CurrentUser:
    """The verified identity of one request.

    Platform roles live here; Agent/Workflow grants belong to their resource owners
    and are never derived from the platform roles carried by this value.
    """

    user_id: UUID
    email: str
    display_name: str
    status: str
    platform_roles: frozenset[str]

    def has_platform_role(self, role: str) -> bool:
        return role in self.platform_roles


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
    """Require an active account with the requested platform role."""
    ensure_active_user(user)
    if not user.has_platform_role(role):
        raise ForbiddenError(
            "This action requires a platform role",
            details={"required_role": str(role)},
        )
    return user


def require_platform_admin(user: CurrentUser) -> CurrentUser:
    return require_platform_role(user, PlatformRole.ADMIN)
