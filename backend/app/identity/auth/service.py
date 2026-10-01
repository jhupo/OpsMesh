from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.identity.auth.models import UserAPIToken
from backend.app.identity.authorization.context import AuthenticatedUser
from backend.app.identity.authorization.errors import AuthenticationError, PermissionDeniedError
from backend.app.identity.authorization.permissions import (
    AccountAction,
    WorkspaceAction,
    role_allows,
)
from backend.app.identity.users.models import User
from backend.app.shared.config import Settings
from backend.app.shared.utils import datetime_or_none
from backend.app.workspaces.members.models import WorkspaceMember

_PASSWORD_HASH = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash("opsmesh-dummy-authentication-password")


@dataclass(frozen=True)
class CreatedUserAPIToken:
    record: UserAPIToken
    token: str





class AuthenticationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def authenticate_user(self, user_id: UUID) -> AuthenticatedUser:
        user = self._session.get(User, user_id, populate_existing=True)
        if user is None or user.status != "active":
            raise AuthenticationError("Authenticated user was not found or is inactive")
        return AuthenticatedUser.from_model(user)

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
