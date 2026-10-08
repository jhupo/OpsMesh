from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.identity.auth.models import UserAPIToken
from backend.app.identity.auth.service import AuthenticationService
from backend.app.identity.authorization.context import AuthenticatedUser
from backend.app.identity.authorization.errors import AuthenticationError, PermissionDeniedError
from backend.app.identity.authorization.permissions import (
    AccountAction,
)
from backend.app.identity.users.avatars import normalize_avatar
from backend.app.identity.users.models import User, UserAvatar
from backend.app.shared.errors import ConflictError

PLATFORM_ADMIN_USERNAME = "superadmin"
PLATFORM_ADMIN_EMAIL = "superadmin@localhost.invalid"




@dataclass(frozen=True)
class ProfileChange:
    display_name: str
    replace_avatar: bool = False
    avatar_base64: str | None = field(default=None, repr=False)
    current_password: str | None = field(default=None, repr=False)
    new_password: str | None = field(default=None, repr=False)



class AccountService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def register_user(
        self,
        *,
        email: str,
        display_name: str,
        password: str,
    ) -> User:
        normalized_email = AuthenticationService.normalize_email(email)
        existing = self._session.scalar(select(User).where(User.email == normalized_email))
        if existing is not None:
            raise ConflictError("Email is already registered")

        user = User(
            email=normalized_email,
            display_name=display_name.strip(),
            password_hash=AuthenticationService.hash_password(password),
        )
        self._session.add(user)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ConflictError("Email is already registered") from exc
        self._session.refresh(user)
        return user

    def create_platform_admin(self) -> tuple[User, str]:
        existing = self._session.scalar(
            select(User).where(User.username == PLATFORM_ADMIN_USERNAME)
        )
        if existing is not None:
            raise ConflictError("The platform administrator is already initialized")
        password = token_urlsafe(32)
        user = User(
            email=PLATFORM_ADMIN_EMAIL,
            username=PLATFORM_ADMIN_USERNAME,
            display_name="Platform Administrator",
            password_hash=AuthenticationService.hash_password(password),
            platform_admin=True,
        )
        self._session.add(user)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ConflictError("The platform administrator is already initialized") from exc
        self._session.refresh(user)
        return user, password

    def reset_platform_admin_password(self) -> tuple[User, str]:
        user = self._session.scalar(
            select(User).where(
                User.username == PLATFORM_ADMIN_USERNAME,
                User.platform_admin.is_(True),
            )
        )
        if user is None or user.status != "active":
            raise AuthenticationError("The platform administrator was not found or is inactive")
        password = token_urlsafe(32)
        user.password_hash = AuthenticationService.hash_password(password)
        now = datetime.now(UTC)
        tokens = self._session.scalars(
            select(UserAPIToken).where(
                UserAPIToken.user_id == user.id,
                UserAPIToken.status == "active",
            )
        ).all()
        for token in tokens:
            token.status = "revoked"
            token.revoked_at = now
        self._session.commit()
        self._session.refresh(user)
        return user, password

    def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
    ) -> User:
        user = self._active_user_for_update(user_id)
        self._replace_password(user, current_password, new_password)
        self._session.commit()
        self._session.refresh(user)
        return user

    def _replace_password(self, user: User, current_password: str, new_password: str) -> None:
        if not user.password_hash or not AuthenticationService.verify_password(
            current_password,
            user.password_hash,
        ):
            raise AuthenticationError("Invalid current password")
        user.password_hash = AuthenticationService.hash_password(new_password)
        now = datetime.now(UTC)
        tokens = self._session.scalars(
            select(UserAPIToken).where(
                UserAPIToken.user_id == user.id,
                UserAPIToken.status == "active",
            )
        ).all()
        for token in tokens:
            token.status = "revoked"
            token.revoked_at = now

    def _active_user_for_update(self, user_id: UUID) -> User:
        user = self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or user.status != "active":
            raise AuthenticationError("Authenticated user was not found or is inactive")
        return user

    def update_profile(self, *, actor: AuthenticatedUser, change: ProfileChange) -> User:
        if not actor.allows_account_action(AccountAction.PROFILE_WRITE):
            raise PermissionDeniedError("API token scope does not allow profile updates")
        if change.new_password is not None and not actor.allows_account_action(
            AccountAction.PASSWORD_CHANGE
        ):
            raise PermissionDeniedError("API token scope does not allow password changes")
        avatar = (
            normalize_avatar(change.avatar_base64)
            if change.replace_avatar and change.avatar_base64 is not None
            else None
        )
        user = self._active_user_for_update(actor.user_id)
        # Validate password before mutating the profile; commit all fields once.
        if change.new_password is not None:
            self._replace_password(user, change.current_password or "", change.new_password)
        user.display_name = change.display_name.strip()
        if change.replace_avatar:
            existing = self._session.get(UserAvatar, user.id)
            if avatar is None:
                if existing is not None:
                    self._session.delete(existing)
                user.avatar_version = None
            else:
                if existing is None:
                    self._session.add(UserAvatar(user_id=user.id, content=avatar))
                else:
                    existing.content = avatar
                user.avatar_version = sha256(avatar).hexdigest()
        self._session.commit()
        self._session.refresh(user)
        return user

    def get_avatar(self, *, actor: AuthenticatedUser) -> bytes | None:
        if not actor.allows_account_action(AccountAction.PROFILE_READ):
            raise PermissionDeniedError("API token scope does not allow profile reads")
        avatar = self._session.get(UserAvatar, actor.user_id)
        return avatar.content if avatar is not None else None

