from collections.abc import Callable
from hmac import compare_digest
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth.context import AuthenticatedUser, WorkspaceContext
from backend.app.auth.errors import AuthenticationError, PermissionDeniedError
from backend.app.auth.permissions import WorkspaceAction
from backend.app.auth.service import AuthorizationService
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session

AUTHORIZATION_HEADER = Header(default=None)
SETTINGS_DEPENDENCY = Depends(get_settings)
USER_ID_HEADER = Header(alias="X-User-ID")
DB_SESSION_DEPENDENCY = Depends(get_db_session)


async def require_internal_token(
    authorization: str | None = AUTHORIZATION_HEADER,
    settings: Settings = SETTINGS_DEPENDENCY,
) -> None:
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    valid = any(compare_digest(token, candidate) for candidate in settings.internal_api_tokens)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authorization token",
        )


async def get_current_user(
    x_user_id: UUID = USER_ID_HEADER,
    session: Session = DB_SESSION_DEPENDENCY,
    _: None = Depends(require_internal_token),  # noqa: B008
) -> AuthenticatedUser:
    try:
        return AuthorizationService(session).authenticate_user(x_user_id)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc


CURRENT_USER_DEPENDENCY = Depends(get_current_user)


def workspace_dependency(action: WorkspaceAction) -> Callable[..., object]:
    async def require_workspace_context(
        workspace_id: UUID,
        current_user: AuthenticatedUser = CURRENT_USER_DEPENDENCY,
        session: Session = DB_SESSION_DEPENDENCY,
    ) -> WorkspaceContext:
        try:
            return AuthorizationService(session).require_workspace(
                user_id=current_user.user_id,
                workspace_id=workspace_id,
                action=action,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc

    return require_workspace_context
