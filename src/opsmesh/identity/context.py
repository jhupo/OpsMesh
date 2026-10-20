"""CurrentUser: the authenticated identity value, without foreign-domain rights."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class CurrentUser:
    """The verified identity of one request.

    Platform roles live here; configuration grants belong to orchestration
    and are never derived from the platform roles carried by this value.
    """

    user_id: UUID
    email: str
    display_name: str
    status: str
    platform_roles: frozenset[str]

    def has_platform_role(self, role: str) -> bool:
        return role in self.platform_roles
