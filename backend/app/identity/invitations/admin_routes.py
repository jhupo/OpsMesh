from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.app.governance.security_events.service import SecurityAuditService
from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.identity.invitations.schemas import UserInvitationRequest, UserInvitationResponse
from backend.app.identity.invitations.service import UserInvitationService
from backend.app.shared.config import Settings, get_settings
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.client_ip import security_request_context

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.post("/user-invitations", response_model=UserInvitationResponse, status_code=201)
def invite_user(
    payload: UserInvitationRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserInvitationResponse:
    invitation = UserInvitationService(session, settings).invite(**payload.model_dump())
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_invited",
        outcome="allowed",
        severity="info",
        reason="User invitation processed",
        user_id=invitation.user_id,
        metadata={"delivery_status": invitation.delivery_status},
    )
    session.commit()
    return UserInvitationResponse.model_validate(invitation)


@router.post("/users/{user_id}/invitation/resend", response_model=UserInvitationResponse)
def resend_user_invitation(
    user_id: UUID,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserInvitationResponse:
    invitation = UserInvitationService(session, settings).resend(user_id)
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_invitation_resent",
        outcome="allowed",
        severity="info",
        reason="User invitation resend processed",
        user_id=user_id,
        metadata={"delivery_status": invitation.delivery_status},
    )
    session.commit()
    return UserInvitationResponse.model_validate(invitation)
