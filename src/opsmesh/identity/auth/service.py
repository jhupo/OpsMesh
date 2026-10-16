"""Authentication service: registration, login, live session checks, revocation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.access import ensure_active_user
from opsmesh.identity.auth.contracts import (
    AccountOpening,
    SecurityEventRecorder,
    UserDirectory,
)
from opsmesh.identity.auth.models import LoginSession, UserCredential
from opsmesh.identity.auth.passwords import hash_password, verify_password
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import AuthenticationError, InvalidCredentialsError
from opsmesh.shared.errors import ConflictError, NotFoundError

DEFAULT_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
LAST_SEEN_REFRESH_SECONDS = 60


@dataclass(frozen=True)
class LoginResult:
    """A freshly created session and the identity it authenticates."""

    session_id: UUID
    user: CurrentUser
    expires_at: datetime


def token_digest(token: UUID) -> str:
    """The stored form of a session token; the token itself is never stored."""
    return hashlib.sha256(token.bytes).hexdigest()


class AuthService:
    """Password login with immediately effective expiry and revocation."""

    def __init__(
        self,
        *,
        account_opening: AccountOpening,
        user_directory: UserDirectory,
        security_events: SecurityEventRecorder,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        if session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be positive")
        self._account_opening = account_opening
        self._user_directory = user_directory
        self._security_events = security_events
        self._session_ttl_seconds = session_ttl_seconds

    def register(
        self,
        session: Session,
        *,
        email: str,
        display_name: str,
        password: str,
    ) -> LoginResult:
        """Open an account and start its first session in one transaction."""
        password_hash = hash_password(password)
        account = self._account_opening.open_account(
            session, email=email, display_name=display_name
        )
        session.add(
            UserCredential(user_id=account.user_id, password_hash=password_hash)
        )
        user = CurrentUser(
            user_id=account.user_id,
            email=account.email,
            display_name=account.display_name,
            status=account.status,
            platform_roles=account.platform_roles,
        )
        return self._start_session(session, user=user)

    def login(self, session: Session, *, email: str, password: str) -> LoginResult:
        """Verify credentials; unknown accounts and wrong passwords are alike.

        A failure raises without recording: the security fact belongs to a
        transaction that survives the rolled-back attempt, which the route
        opens through ``record_login_failure``.
        """
        identity = self._user_directory.find_by_email(session, email)
        if identity is None:
            raise InvalidCredentialsError()
        credential = session.scalar(
            select(UserCredential).where(UserCredential.user_id == identity.user_id)
        )
        if credential is None or not verify_password(password, credential.password_hash):
            raise InvalidCredentialsError()
        ensure_active_user(identity)
        return self._start_session(session, user=identity)

    def record_login_failure(self, session: Session, *, email: str) -> None:
        """Record one failed login in the caller's own committed transaction."""
        identity = self._user_directory.find_by_email(session, email)
        if identity is None:
            self._security_events.record(
                session,
                kind="login.unknown_account",
                severity="warning",
                summary="Login attempt for an unknown account",
                details={"email_domain": email.rsplit("@", 1)[-1]},
                user_id=None,
            )
            return
        self._security_events.record(
            session,
            kind="login.invalid_credentials",
            severity="warning",
            summary="Login attempt with invalid credentials",
            details={"user_id": str(identity.user_id)},
            user_id=identity.user_id,
        )

    def authenticate(self, session: Session, *, token: UUID) -> CurrentUser:
        """Resolve one session token; expiry and revocation apply immediately."""
        login_session = session.scalar(
            select(LoginSession).where(LoginSession.token_hash == token_digest(token))
        )
        if login_session is None:
            raise AuthenticationError("The session does not exist")
        now = datetime.now(UTC)
        if login_session.revoked_at is not None or login_session.expires_at <= now:
            raise AuthenticationError("The session is no longer valid")
        identity = self._user_directory.load_identity(session, login_session.user_id)
        if identity is None:
            raise AuthenticationError("The session user no longer exists")
        ensure_active_user(identity)
        self._touch(session, login_session=login_session, now=now)
        return identity

    def logout(self, session: Session, *, token: UUID) -> None:
        """Revoke the presenting session; an unknown token is not an error."""
        login_session = session.scalar(
            select(LoginSession).where(LoginSession.token_hash == token_digest(token))
        )
        if login_session is None or login_session.revoked_at is not None:
            return
        login_session.revoked_at = datetime.now(UTC)

    def revoke_session(self, session: Session, *, user_id: UUID, session_id: UUID) -> None:
        """Revoke one session of one user; another user's session is invisible."""
        login_session = session.get(LoginSession, session_id)
        if login_session is None or login_session.user_id != user_id:
            raise NotFoundError("The session does not exist")
        if login_session.revoked_at is None:
            login_session.revoked_at = datetime.now(UTC)

    def list_sessions(
        self, session: Session, *, user_id: UUID
    ) -> list[tuple[UUID, datetime, datetime | None]]:
        """The caller's own sessions: id, expiry and revocation."""
        rows = session.scalars(
            select(LoginSession)
            .where(LoginSession.user_id == user_id)
            .order_by(LoginSession.created_at.desc())
        )
        return [(row.id, row.expires_at, row.revoked_at) for row in rows]

    def change_password(
        self, session: Session, *, user_id: UUID, current_password: str, new_password: str
    ) -> None:
        """Rotate the verifier; every existing session of the user is revoked."""
        credential = session.scalar(
            select(UserCredential).where(UserCredential.user_id == user_id)
        )
        if credential is None or not verify_password(current_password, credential.password_hash):
            raise InvalidCredentialsError()
        if verify_password(new_password, credential.password_hash):
            raise ConflictError("The new password must differ from the current one")
        credential.password_hash = hash_password(new_password)
        now = datetime.now(UTC)
        for login_session in session.scalars(
            select(LoginSession).where(
                LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None)
            )
        ):
            login_session.revoked_at = now

    def _start_session(self, session: Session, *, user: CurrentUser) -> LoginResult:
        token = uuid4()
        expires_at = datetime.now(UTC) + timedelta(seconds=self._session_ttl_seconds)
        session.add(
            LoginSession(
                id=token,
                user_id=user.user_id,
                token_hash=token_digest(token),
                expires_at=expires_at,
            )
        )
        return LoginResult(session_id=token, user=user, expires_at=expires_at)

    def _touch(self, session: Session, *, login_session: LoginSession, now: datetime) -> None:
        if (
            login_session.last_seen_at is not None
            and (now - login_session.last_seen_at).total_seconds() < LAST_SEEN_REFRESH_SECONDS
        ):
            return
        login_session.last_seen_at = now
