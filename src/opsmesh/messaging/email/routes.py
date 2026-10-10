from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from opsmesh.governance.security_events.service import SecurityAuditService
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.messaging.email.schemas import (
    EmailRecipient,
    MailConfigurationResponse,
    MailConfigurationUpdate,
)
from opsmesh.messaging.email.service import PlatformMailService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.client_ip import security_request_context

DB_SESSION_DEPENDENCY = Depends(get_db_session)
SETTINGS_DEPENDENCY = Depends(get_settings)

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/system/mail", response_model=MailConfigurationResponse)
def get_mail_configuration(
    session: Session = DB_SESSION_DEPENDENCY, settings: Settings = SETTINGS_DEPENDENCY
) -> MailConfigurationResponse:
    return PlatformMailService(session, settings).configuration()


@router.put("/system/mail", response_model=MailConfigurationResponse)
def save_mail_configuration(
    payload: MailConfigurationUpdate,
    request: Request,
    session: Session = DB_SESSION_DEPENDENCY,
    settings: Settings = SETTINGS_DEPENDENCY,
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
    session: Session = DB_SESSION_DEPENDENCY,
    settings: Settings = SETTINGS_DEPENDENCY,
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
