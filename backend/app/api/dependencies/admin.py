from hmac import compare_digest

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.api.client_ip import security_request_context
from backend.app.core.config import Settings, get_settings
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.errors import AuthenticationError
from backend.app.domains.access.service import AuthorizationService
from backend.app.observability.audit.security_events import SecurityAuditService

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
            user = AuthorizationService(session).authenticate_user_token(provided_token, settings)
        except AuthenticationError:
            user = None
        if user is not None and user.platform_admin:
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
