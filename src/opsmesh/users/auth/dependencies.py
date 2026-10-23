"""Authenticate API requests with the user's login session."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.users.access import CurrentUser
from opsmesh.users.auth.service import AuthService
from opsmesh.users.errors import AuthenticationError

SESSION_HEADER = "X-Session-ID"

DbSession = Annotated[Session, Depends(get_db_session)]


def get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if not isinstance(service, AuthService):
        raise AuthenticationError("Authentication is unavailable")
    return service


def current_user(
    session: DbSession,
    request: Request,
    x_session_id: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
) -> CurrentUser:
    if x_session_id is None:
        raise AuthenticationError("A login session is required")
    try:
        token = UUID(x_session_id)
    except ValueError as exc:
        raise AuthenticationError("The session token is malformed") from exc
    with session_transaction(session):
        return get_auth_service(request).authenticate(session, token=token)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
