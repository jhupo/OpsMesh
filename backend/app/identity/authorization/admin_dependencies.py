from hmac import compare_digest

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.governance.security_events.service import SecurityAuditService
from backend.app.identity.auth.service import AuthenticationService
from backend.app.identity.authorization.errors import AuthenticationError
from backend.app.shared.config import Settings, get_settings
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.client_ip import security_request_context

SETTINGS_DEPENDENCY = Depends(get_settings)
DB_SESSION_DEPENDENCY = Depends(get_db_session)


async def require_platform_admin(
    request: Request,
    authorization: str | None = Header(default=None),
    settings: Settings = SETTINGS_DEPENDENCY,
    session: Session = DB_SESSION_DEPENDENCY,
) -> None:
    configured_token = settings.platform_admin_token
    provided_token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if configured_token and compare_digest(provided_token, configured_token):
        return
    if provided_token:
        try:
            user = AuthenticationService(session).authenticate_user_token(provided_token, settings)
        except AuthenticationError:
            user = None
        if user is not None and user.platform_admin and not user.uses_restricted_token:
            session.commit()
            return
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="auth.platform_admin.rejected",
        outcome="denied",
        severity="critical",
        reason="Invalid or missing platform admin token",
        metadata={"has_authorization_header": bool(authorization)},
    )
    session.commit()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing platform admin token",
    )
