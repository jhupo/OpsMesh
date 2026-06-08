from collections.abc import Callable
from hmac import compare_digest
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.auth.context import AuthenticatedUser, WorkspaceContext
from backend.app.auth.errors import AuthenticationError, PermissionDeniedError
from backend.app.auth.permissions import WorkspaceAction
from backend.app.auth.service import AuthorizationService
from backend.app.core.config import Settings, get_settings
from backend.app.core.request_context import set_log_context
from backend.app.db.session import get_db_session
from backend.app.security.service import SecurityAuditService

AUTHORIZATION_HEADER = Header(default=None)
SETTINGS_DEPENDENCY = Depends(get_settings)
USER_ID_HEADER = Header(default=None, alias="X-User-ID")
DB_SESSION_DEPENDENCY = Depends(get_db_session)


async def require_internal_token(
    request: Request,
    authorization: str | None = AUTHORIZATION_HEADER,
    settings: Settings = SETTINGS_DEPENDENCY,
    session: Session = DB_SESSION_DEPENDENCY,
) -> None:
    token = _bearer_token(authorization)
    valid = _is_internal_token(token, settings)
    if not valid:
        SecurityAuditService(session).record_request_event(
            request=request,
            action="auth.internal_token.rejected",
            outcome="denied",
            severity="warning",
            reason="Invalid or missing authorization token",
            metadata={"has_authorization_header": bool(authorization)},
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authorization token",
        )


async def get_current_user(
    request: Request,
    authorization: str | None = AUTHORIZATION_HEADER,
    x_user_id: UUID | None = USER_ID_HEADER,
    settings: Settings = SETTINGS_DEPENDENCY,
    session: Session = DB_SESSION_DEPENDENCY,
) -> AuthenticatedUser:
    token = _bearer_token(authorization)
    service = AuthorizationService(session)
    if token:
        try:
            user = service.authenticate_user_token(token, settings)
            session.commit()
            set_log_context(user_id=user.user_id)
            return user
        except AuthenticationError as exc:
            session.rollback()
            if not _is_internal_token(token, settings):
                action = (
                    "auth.internal_token.rejected"
                    if x_user_id is not None
                    else "auth.user_token.rejected"
                )
                metadata: dict[str, object] = {"has_authorization_header": True}
                if action == "auth.user_token.rejected":
                    metadata["token_fingerprint"] = (
                        AuthorizationService.fingerprint_user_token(token)
                    )
                _record_auth_failure(
                    session=session,
                    request=request,
                    action=action,
                    reason=exc.message,
                    metadata=metadata,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing authorization token",
                ) from exc

    if not _is_internal_token(token, settings):
        _record_auth_failure(
            session=session,
            request=request,
            action="auth.internal_token.rejected",
            reason="Invalid or missing authorization token",
            metadata={"has_authorization_header": bool(authorization)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authorization token",
        )
    if x_user_id is None:
        _record_auth_failure(
            session=session,
            request=request,
            action="auth.user.rejected",
            reason="Missing X-User-ID for internal authentication",
            metadata={"auth_scheme": "internal_token"},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Missing X-User-ID for internal authentication",
        )
    try:
        user = service.authenticate_user(x_user_id)
        set_log_context(user_id=user.user_id)
        return user
    except AuthenticationError as exc:
        _record_auth_failure(
            session=session,
            request=request,
            action="auth.user.rejected",
            reason=exc.message,
            user_id=x_user_id,
            metadata={"auth_scheme": "internal_token"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc


CURRENT_USER_DEPENDENCY = Depends(get_current_user)


def workspace_dependency(action: WorkspaceAction) -> Callable[..., object]:
    async def require_workspace_context(
        request: Request,
        workspace_id: UUID,
        current_user: AuthenticatedUser = CURRENT_USER_DEPENDENCY,
        session: Session = DB_SESSION_DEPENDENCY,
    ) -> WorkspaceContext:
        try:
            context = AuthorizationService(session).require_workspace(
                user_id=current_user.user_id,
                workspace_id=workspace_id,
                action=action,
            )
            set_log_context(
                user_id=current_user.user_id,
                workspace_id=context.workspace.id,
            )
            return context
        except PermissionDeniedError as exc:
            SecurityAuditService(session).record_request_event(
                request=request,
                action="auth.workspace.rejected",
                outcome="denied",
                severity="warning",
                reason=exc.message,
                workspace_id=workspace_id,
                user_id=current_user.user_id,
                metadata={"required_action": action.value},
            )
            session.commit()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc

    return require_workspace_context


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return ""
    return credentials.strip()


def _is_internal_token(token: str, settings: Settings) -> bool:
    return bool(token) and any(
        compare_digest(token, candidate) for candidate in settings.internal_api_tokens
    )


def _record_auth_failure(
    *,
    session: Session,
    request: Request,
    action: str,
    reason: str,
    user_id: UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    SecurityAuditService(session).record_request_event(
        request=request,
        action=action,
        outcome="denied",
        severity="warning",
        reason=reason,
        user_id=user_id,
        metadata=metadata,
    )
    session.commit()
