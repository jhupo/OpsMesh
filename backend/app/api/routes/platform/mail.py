from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.app.api.client_ip import security_request_context
from backend.app.api.schemas.platform.mail import (
    EmailRecipient,
    UserInvitationRequest,
    UserInvitationResponse,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.invitations import UserInvitationService
from backend.app.domains.platform.mail import (
    MailConfigurationResponse,
    MailConfigurationUpdate,
    PlatformMailService,
)
from backend.app.observability.audit.security_events import SecurityAuditService

router = APIRouter()


@router.get("/system/mail", response_model=MailConfigurationResponse)
def get_mail_configuration(
    session: Session = Depends(get_db_session), settings: Settings = Depends(get_settings)
) -> MailConfigurationResponse:
    return PlatformMailService(session, settings).configuration()


@router.put("/system/mail", response_model=MailConfigurationResponse)
def save_mail_configuration(
    payload: MailConfigurationUpdate,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MailConfigurationResponse:
    result = PlatformMailService(session, settings).save(payload)
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="platform.mail_settings_updated",
        outcome="allowed",
        severity="warning",
        reason="Platform mail settings updated",
    )
    session.commit()
    return result


@router.post("/system/mail/test")
def test_mail_configuration(
    payload: EmailRecipient,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    PlatformMailService(session, settings).send(
        recipient=payload.email,
        subject="OpsMesh 邮件测试 / Email test",
        body="OpsMesh 邮件发送配置测试成功。SMTP configuration test succeeded.",
    )
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="platform.mail_test_sent",
        outcome="allowed",
        severity="info",
        reason="Platform test email sent",
    )
    session.commit()
    return {"sent": True}


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
