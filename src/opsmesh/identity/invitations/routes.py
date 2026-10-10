from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from opsmesh.governance.security_events.service import SecurityAuditService
from opsmesh.identity.auth.schemas import CurrentUserResponse
from opsmesh.identity.invitations.schemas import AcceptUserInvitationRequest
from opsmesh.identity.invitations.service import UserInvitationService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.client_ip import security_request_context

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/invitations/accept", response_model=CurrentUserResponse)
def accept_user_invitation(
    payload: AcceptUserInvitationRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> CurrentUserResponse:
    user = UserInvitationService(session, settings).accept(
        token=payload.token.get_secret_value(),
        password=payload.password.get_secret_value(),
        display_name=payload.display_name,
        username=payload.username,
    )
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_invitation_accepted",
        outcome="allowed",
        severity="info",
        reason="Invited user activated their account",
        user_id=user.id,
    )
    session.commit()
    return CurrentUserResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        platform_admin=user.platform_admin,
        avatar_version=user.avatar_version,
    )
