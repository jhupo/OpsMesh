"""Identity assembly: account opening, identity directory and first administrator.

This root-level module composes the users and quotas owners behind the
contracts identity.auth and identity.invitations declare, so neither
submodule imports the other. Bootstrap builds these objects with the
configured defaults and injects them into the assembled services.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.auth.contracts import (
    OpenedAccount,
)
from opsmesh.identity.auth.models import UserCredential
from opsmesh.identity.auth.passwords import hash_password
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.invitations.contracts import (
    OpenedInvitationAccount,
)
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile

PLATFORM_MEMBER = "member"
PLATFORM_ADMINISTRATOR = "admin"
FIRST_ADMINISTRATOR_ROLES = frozenset({PLATFORM_ADMINISTRATOR, PLATFORM_MEMBER})


@dataclass(frozen=True)
class AccountDefaults:
    """The platform-configured defaults applied to every new account."""

    platform_roles: frozenset[str] = frozenset({PLATFORM_MEMBER})
    token_limit: int | None = None
    storage_limit_bytes: int | None = None


class ProvisionedAccountOpening:
    """Open the profile and the quota ledger of one new account."""

    def __init__(self, defaults: AccountDefaults) -> None:
        self._defaults = defaults

    def open_account(
        self, session: Session, *, email: str, display_name: str
    ) -> OpenedAccount:
        profile = users.create_user(
            session,
            email=email,
            display_name=display_name,
            platform_roles=self._defaults.platform_roles,
        )
        quotas.ensure_ledger(
            session,
            user_id=profile.id,
            token_limit=self._defaults.token_limit,
            storage_limit_bytes=self._defaults.storage_limit_bytes,
        )
        return OpenedAccount(
            user_id=profile.id,
            email=profile.email,
            display_name=profile.display_name,
            status=profile.status,
            platform_roles=frozenset(profile.platform_roles),
        )


class UserIdentityDirectory:
    """Read the current identity value of one account for authentication."""

    def load_identity(self, session: Session, user_id: UUID) -> CurrentUser | None:
        return users.load_identity(session, user_id)

    def find_by_email(self, session: Session, email: str) -> CurrentUser | None:
        profile = users.find_by_email(session, email)
        if profile is None:
            return None
        return users.identity_of(profile)


class InvitationAccountService:
    """Open an invited account through the assembled authentication service."""

    def __init__(self, auth_service: AuthService) -> None:
        self._auth_service = auth_service

    def open_account(
        self, session: Session, *, email: str, display_name: str, password: str
    ) -> OpenedInvitationAccount:
        result = self._auth_service.register(
            session, email=email, display_name=display_name, password=password
        )
        return OpenedInvitationAccount(
            user_id=result.user.user_id,
            session_id=result.session_id,
            expires_at=result.expires_at,
        )


def provision_first_admin(
    session: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    defaults: AccountDefaults,
) -> UserProfile:
    """Create the first administrator with its credential and quota ledger.

    The bootstrap CLI calls this on an empty platform; the account carries the
    administrator role from the start and no session is issued for it.
    """
    profile = users.create_user(
        session,
        email=email,
        display_name=display_name,
        platform_roles=FIRST_ADMINISTRATOR_ROLES,
    )
    session.add(UserCredential(user_id=profile.id, password_hash=hash_password(password)))
    quotas.ensure_ledger(
        session,
        user_id=profile.id,
        token_limit=defaults.token_limit,
        storage_limit_bytes=defaults.storage_limit_bytes,
    )
    return profile


__all__ = [
    "AccountDefaults",
    "FIRST_ADMINISTRATOR_ROLES",
    "InvitationAccountService",
    "ProvisionedAccountOpening",
    "UserIdentityDirectory",
    "provision_first_admin",
]
