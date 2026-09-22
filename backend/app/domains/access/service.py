from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import ConflictError
from backend.app.core.utils import datetime_or_none
from backend.app.domains.access.avatars import normalize_avatar
from backend.app.domains.access.context import AuthenticatedUser, WorkspaceContext
from backend.app.domains.access.errors import (
    AuthenticationError,
    PermissionDeniedError,
)
from backend.app.domains.access.models import User, UserAPIToken, UserAvatar
from backend.app.domains.access.permissions import AccountAction, WorkspaceAction, role_allows
from backend.app.domains.workspace.tenants.models import Workspace, WorkspaceMember

_PASSWORD_HASH = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash("opsmesh-dummy-authentication-password")
PLATFORM_ADMIN_USERNAME = "superadmin"
PLATFORM_ADMIN_EMAIL = "superadmin@localhost.invalid"


@dataclass(frozen=True)
class CreatedUserAPIToken:
    record: UserAPIToken
    token: str


@dataclass(frozen=True)
class ProfileChange:
    display_name: str
    replace_avatar: bool = False
    avatar_base64: str | None = field(default=None, repr=False)
    current_password: str | None = field(default=None, repr=False)
    new_password: str | None = field(default=None, repr=False)


class AuthorizationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def authenticate_user(self, user_id: UUID) -> AuthenticatedUser:
        user = self._session.get(User, user_id, populate_existing=True)
        if user is None or user.status != "active":
            raise AuthenticationError("Authenticated user was not found or is inactive")
        return AuthenticatedUser.from_model(user)

    def register_user(
        self,
        *,
        email: str,
        display_name: str,
        password: str,
    ) -> User:
        normalized_email = self.normalize_email(email)
        existing = self._session.scalar(select(User).where(User.email == normalized_email))
        if existing is not None:
            raise ConflictError("Email is already registered")

        user = User(
            email=normalized_email,
            display_name=display_name.strip(),
            password_hash=self.hash_password(password),
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
            password_hash=self.hash_password(password),
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
        user.password_hash = self.hash_password(password)
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

    def login_with_password(
        self,
        *,
        email: str | None = None,
        username: str | None = None,
        password: str,
        settings: Settings,
        token_name: str = "password login",
    ) -> CreatedUserAPIToken:
        if (email is None) == (username is None):
            raise ValueError("Exactly one login identifier is required")
        if email is not None:
            user = self._session.scalar(
                select(User).where(User.email == self.normalize_email(email))
            )
        else:
            assert username is not None
            user = self._session.scalar(select(User).where(User.username == username.strip()))
        active_hash = (
            user.password_hash
            if user is not None and user.status == "active" and user.password_hash
            else _DUMMY_PASSWORD_HASH
        )
        password_matches = self.verify_password(password, active_hash)
        if (
            user is None
            or user.status != "active"
            or not user.password_hash
            or not password_matches
        ):
            raise AuthenticationError("Invalid email or password")
        return self.create_user_api_token(
            user_id=user.id,
            name=token_name,
            settings=settings,
        )

    def create_user_api_token(
        self,
        *,
        user_id: UUID,
        name: str,
        settings: Settings,
        expires_at: datetime | None = None,
        scopes: dict[str, object] | None = None,
        actor: AuthenticatedUser | None = None,
    ) -> CreatedUserAPIToken:
        self.authenticate_user(user_id)
        normalized_expiry = self._validate_token_expiry(expires_at)
        normalized_scopes = self._validate_token_scopes(
            user_id=user_id,
            scopes=scopes,
            actor=actor,
        )
        token = f"ccut_{token_urlsafe(32)}"
        record = UserAPIToken(
            user_id=user_id,
            name=name,
            token_hash=self.hash_user_token(token, settings),
            fingerprint=self.fingerprint_user_token(token),
            scopes=normalized_scopes,
            expires_at=normalized_expiry,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return CreatedUserAPIToken(record=record, token=token)

    def rotate_user_api_token(
        self,
        *,
        user_id: UUID,
        token_id: UUID,
        settings: Settings,
        actor: AuthenticatedUser,
        name: str | None = None,
        expires_at: datetime | None = None,
    ) -> CreatedUserAPIToken | None:
        self.authenticate_user(user_id)
        token = self._session.scalar(
            select(UserAPIToken)
            .where(
                UserAPIToken.id == token_id,
                UserAPIToken.user_id == user_id,
            )
            .with_for_update()
        )
        if token is None or token.status != "active" or token.revoked_at is not None:
            return None
        normalized_scopes = self._validate_token_scopes(
            user_id=user_id,
            scopes=dict(token.scopes) if isinstance(token.scopes, dict) else None,
            actor=actor,
        )
        normalized_expiry = self._validate_token_expiry(
            expires_at if expires_at is not None else token.expires_at
        )
        raw_token = f"ccut_{token_urlsafe(32)}"
        replacement = UserAPIToken(
            user_id=user_id,
            name=name.strip() if name is not None else token.name,
            token_hash=self.hash_user_token(raw_token, settings),
            fingerprint=self.fingerprint_user_token(raw_token),
            scopes=normalized_scopes,
            expires_at=normalized_expiry,
        )
        token.status = "revoked"
        token.revoked_at = datetime.now(UTC)
        self._session.add(replacement)
        self._session.commit()
        self._session.refresh(replacement)
        return CreatedUserAPIToken(record=replacement, token=raw_token)

    def list_user_api_tokens(self, user_id: UUID) -> list[UserAPIToken]:
        self.authenticate_user(user_id)
        return list(
            self._session.scalars(
                select(UserAPIToken)
                .where(UserAPIToken.user_id == user_id)
                .order_by(UserAPIToken.created_at.desc(), UserAPIToken.id.desc())
            ).all()
        )

    def revoke_user_api_token(self, *, user_id: UUID, token_id: UUID) -> UserAPIToken | None:
        self.authenticate_user(user_id)
        token = self._session.get(UserAPIToken, token_id)
        if token is None or token.user_id != user_id:
            return None
        if token.status != "revoked":
            token.status = "revoked"
            token.revoked_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(token)
        return token

    def revoke_all_user_api_tokens(self, *, user_id: UUID) -> list[UserAPIToken]:
        self.authenticate_user(user_id)
        now = datetime.now(UTC)
        tokens = list(
            self._session.scalars(
                select(UserAPIToken).where(
                    UserAPIToken.user_id == user_id,
                    UserAPIToken.status != "revoked",
                )
            ).all()
        )
        for token in tokens:
            token.status = "revoked"
            token.revoked_at = now
        self._session.commit()
        return tokens

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
        if not user.password_hash or not self.verify_password(
            current_password,
            user.password_hash,
        ):
            raise AuthenticationError("Invalid current password")
        user.password_hash = self.hash_password(new_password)
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

    def authenticate_user_token(self, raw_token: str, settings: Settings) -> AuthenticatedUser:
        token = self._session.scalar(
            select(UserAPIToken).where(
                UserAPIToken.token_hash == self.hash_user_token(raw_token, settings),
            )
        )
        return self._authenticated_token(token, record_use=True)

    def refresh_authenticated_user(self, current: AuthenticatedUser) -> AuthenticatedUser:
        """Recheck subscriptions without retaining credentials or writing token usage."""
        if current.token_id is None:
            return self.authenticate_user(current.user_id)
        token = self._session.scalar(
            select(UserAPIToken)
            .where(UserAPIToken.id == current.token_id, UserAPIToken.user_id == current.user_id)
            .execution_options(populate_existing=True)
        )
        return self._authenticated_token(token, record_use=False)

    def _authenticated_token(
        self, token: UserAPIToken | None, *, record_use: bool
    ) -> AuthenticatedUser:
        now = datetime.now(UTC)
        if token is None:
            raise AuthenticationError("Invalid or inactive user token")
        if token.status != "active" or token.revoked_at is not None:
            raise AuthenticationError("Invalid or inactive user token")
        expires_at = datetime_or_none(token.expires_at)
        if expires_at is not None and expires_at <= now:
            raise AuthenticationError("Invalid or inactive user token")
        user = self._session.get(User, token.user_id, populate_existing=True)
        if user is None or user.status != "active":
            raise AuthenticationError("Invalid or inactive user token")
        if record_use:
            token.last_used_at = now
            self._session.flush()
        scopes = dict(token.scopes) if isinstance(token.scopes, dict) else None
        return AuthenticatedUser.from_model(
            user,
            token_id=token.id,
            token_scopes=scopes,
        )

    def require_workspace(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        action: WorkspaceAction,
        authenticated_user: AuthenticatedUser | None = None,
    ) -> WorkspaceContext:
        user = authenticated_user or self.authenticate_user(user_id)
        if user.user_id != user_id:
            raise PermissionDeniedError("Authenticated user does not match workspace subject")
        if not user.allows_workspace_action(workspace_id, action):
            raise PermissionDeniedError("API token scope does not allow this workspace action")
        statement = (
            select(Workspace, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.status == "active",
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            raise PermissionDeniedError("User is not an active member of this workspace")

        workspace, membership = row
        if not role_allows(membership.role, action):
            raise PermissionDeniedError("Workspace role does not allow this action")

        return WorkspaceContext(user=user, workspace=workspace, membership=membership)

    def _validate_token_scopes(
        self,
        *,
        user_id: UUID,
        scopes: dict[str, object] | None,
        actor: AuthenticatedUser | None,
    ) -> dict[str, object] | None:
        if scopes is None:
            if actor is not None and actor.uses_restricted_token:
                raise PermissionDeniedError(
                    "Restricted API tokens cannot create unrestricted tokens"
                )
            return None
        raw_workspace_ids = scopes.get("workspace_ids", [])
        raw_workspace_actions = scopes.get("workspace_actions", [])
        raw_account_actions = scopes.get("account_actions", [])
        if not isinstance(raw_workspace_ids, list):
            raise PermissionDeniedError("Token workspace_ids scope is invalid")
        if not isinstance(raw_workspace_actions, list):
            raise PermissionDeniedError("Token workspace_actions scope is invalid")
        if not isinstance(raw_account_actions, list):
            raise PermissionDeniedError("Token account_actions scope is invalid")
        try:
            workspace_ids = [UUID(str(item)) for item in raw_workspace_ids]
            workspace_actions = [WorkspaceAction(str(item)) for item in raw_workspace_actions]
            account_actions = [AccountAction(str(item)) for item in raw_account_actions]
        except (TypeError, ValueError) as exc:
            raise PermissionDeniedError("Token scope contains an unsupported value") from exc
        if bool(workspace_ids) != bool(workspace_actions):
            raise PermissionDeniedError(
                "Token workspace_ids and workspace_actions must be granted together"
            )
        for workspace_id in workspace_ids:
            membership = self._session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                    WorkspaceMember.status == "active",
                )
            )
            if membership is None:
                raise PermissionDeniedError(
                    "Token cannot be scoped to a workspace without active membership"
                )
            for action in workspace_actions:
                if not role_allows(membership.role, action):
                    raise PermissionDeniedError("Token scope exceeds the user's workspace role")
                if actor is not None and not actor.allows_workspace_action(
                    workspace_id,
                    action,
                ):
                    raise PermissionDeniedError("Token scope exceeds the calling token scope")
        if actor is not None:
            for account_action in account_actions:
                if not actor.allows_account_action(account_action):
                    raise PermissionDeniedError("Token scope exceeds the calling token scope")
        return {
            "workspace_ids": sorted(str(item) for item in set(workspace_ids)),
            "workspace_actions": sorted(item.value for item in set(workspace_actions)),
            "account_actions": sorted(item.value for item in set(account_actions)),
        }

    @staticmethod
    def _validate_token_expiry(expires_at: datetime | None) -> datetime | None:
        normalized = datetime_or_none(expires_at)
        if normalized is not None and normalized <= datetime.now(UTC):
            raise PermissionDeniedError("API token expiry must be in the future")
        return normalized

    def ensure_resource_workspace(
        self,
        *,
        resource_workspace_id: UUID,
        expected_workspace_id: UUID,
    ) -> None:
        if resource_workspace_id != expected_workspace_id:
            raise PermissionDeniedError("Resource does not belong to the requested workspace")

    @staticmethod
    def hash_user_token(token: str, settings: Settings) -> str:
        material = f"{settings.token_hash_pepper}:{token}"
        return sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def fingerprint_user_token(token: str) -> str:
        return f"sha256:{sha256(token.encode('utf-8')).hexdigest()[:32]}"

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def hash_password(password: str) -> str:
        return _PASSWORD_HASH.hash(password)

    @staticmethod
    def verify_password(password: str, encoded_hash: str) -> bool:
        try:
            return _PASSWORD_HASH.verify(password, encoded_hash)
        except (UnknownHashError, ValueError):
            return False
