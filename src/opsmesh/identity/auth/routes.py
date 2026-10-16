"""Registration, login, logout, password and login session APIs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status

from opsmesh.identity.access import ensure_active_user
from opsmesh.identity.auth.dependencies import (
    SESSION_HEADER,
    CurrentUserDep,
    DbSession,
    get_auth_service,
)
from opsmesh.identity.auth.schemas import (
    AuthenticatedUser,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    SessionGrant,
    SessionInfo,
)
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import InvalidCredentialsError
from opsmesh.shared.db.session import session_transaction

router = APIRouter(prefix="/auth", tags=["identity"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _grant_user(user: CurrentUser) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        platform_roles=sorted(user.platform_roles),
    )


@router.post(
    "/register",
    response_model=SessionGrant,
    status_code=status.HTTP_201_CREATED,
    operation_id="identity.auth.register",
)
def register(
    payload: RegisterRequest,
    session: DbSession,
    auth_service: AuthServiceDep,
) -> SessionGrant:
    """Open an account and return its first session."""
    with session_transaction(session):
        result = auth_service.register(
            session,
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password,
        )
    return SessionGrant(
        session_id=result.session_id,
        expires_at=result.expires_at,
        user=_grant_user(result.user),
    )


@router.post("/login", response_model=SessionGrant, operation_id="identity.auth.login")
def login(
    payload: LoginRequest,
    session: DbSession,
    auth_service: AuthServiceDep,
) -> SessionGrant:
    try:
        with session_transaction(session):
            result = auth_service.login(session, email=payload.email, password=payload.password)
    except InvalidCredentialsError:
        # The failed attempt rolls back; its security fact is committed on its
        # own so a credential-guessing flood still leaves evidence.
        with session_transaction(session):
            auth_service.record_login_failure(session, email=payload.email)
        raise
    return SessionGrant(
        session_id=result.session_id,
        expires_at=result.expires_at,
        user=_grant_user(result.user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="identity.auth.logout")
def logout(
    session: DbSession,
    auth_service: AuthServiceDep,
    user: CurrentUserDep,
    x_session_id: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
) -> Response:
    """Revoke the presenting session; the identity channel stays available."""
    _ = ensure_active_user(user)
    if x_session_id is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    with session_transaction(session):
        auth_service.logout(session, token=UUID(x_session_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/sessions", response_model=list[SessionInfo], operation_id="identity.auth.sessions.list"
)
def list_sessions(
    session: DbSession, auth_service: AuthServiceDep, user: CurrentUserDep
) -> list[SessionInfo]:
    rows = auth_service.list_sessions(session, user_id=user.user_id)
    return [SessionInfo(session_id=row[0], expires_at=row[1], revoked_at=row[2]) for row in rows]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="identity.auth.sessions.revoke",
)
def revoke_session(
    session_id: UUID,
    session: DbSession,
    auth_service: AuthServiceDep,
    user: CurrentUserDep,
) -> Response:
    with session_transaction(session):
        auth_service.revoke_session(session, user_id=user.user_id, session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="identity.auth.change_password",
)
def change_password(
    payload: ChangePasswordRequest,
    session: DbSession,
    auth_service: AuthServiceDep,
    user: CurrentUserDep,
) -> Response:
    with session_transaction(session):
        auth_service.change_password(
            session,
            user_id=user.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
