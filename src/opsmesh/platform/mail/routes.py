"""SMTP administration, restricted send, status and cancellation APIs."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.platform.mail import service
from opsmesh.platform.mail.models import MAIL_STATUSES, MailIntent, SmtpConfiguration
from opsmesh.platform.mail.schemas import (
    MailIntentPage,
    MailIntentView,
    ReconcileMailRequest,
    SendMailRequest,
    SmtpConfigurationRequest,
    SmtpConfigurationView,
    TestMailRequest,
)
from opsmesh.platform.mail.service import (
    PLATFORM_TEST_BODY,
    PLATFORM_TEST_SOURCE,
    PLATFORM_TEST_SUBJECT,
    DelegatedMailSend,
    PlatformTestAuthorization,
    SmtpConfigurationInput,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.shared.security.secrets import SecretCipher

router = APIRouter(prefix="/platform/mail", tags=["platform"])


def get_mail_cipher(request: Request) -> SecretCipher:
    cipher = getattr(request.app.state, "secret_cipher", None)
    if not isinstance(cipher, SecretCipher):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mail encryption is not assembled",
        )
    return cipher


def require_delegated_mail_send() -> DelegatedMailSend:
    """Placeholder for the verified delegated send; bootstrap injects it."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Delegated mail sending is not assembled",
    )


CipherDep = Annotated[SecretCipher, Depends(get_mail_cipher)]
DelegatedSendDep = Annotated[DelegatedMailSend, Depends(require_delegated_mail_send)]


def _smtp_view(configuration: SmtpConfiguration) -> SmtpConfigurationView:
    return SmtpConfigurationView(
        host=configuration.host,
        port=configuration.port,
        security=configuration.security,
        username=configuration.username,
        from_email=configuration.from_email,
        from_name=configuration.from_name,
        timeout_seconds=configuration.timeout_seconds,
        password_configured=configuration.password_ciphertext is not None,
        updated_at=configuration.updated_at,
    )


def _intent_view(intent: MailIntent) -> MailIntentView:
    return MailIntentView(
        intent_id=intent.id,
        source=intent.source,
        source_ref=intent.source_ref,
        source_revision=intent.source_revision,
        recipient=intent.recipient,
        subject=intent.subject,
        status=intent.status,
        attempts=intent.attempts,
        outcome_unknown=intent.outcome_unknown,
        failure_reason=intent.failure_reason,
        created_at=intent.created_at,
        sent_at=intent.sent_at,
        reconciled_at=intent.reconciled_at,
        reconciliation_note=intent.reconciliation_note,
    )


@router.get("/smtp", response_model=SmtpConfigurationView, operation_id="platform.mail.smtp.read")
def read_smtp_configuration(
    session: DbSession, user: CurrentUserDep
) -> SmtpConfigurationView:
    require_platform_admin(user)
    configuration = service.get_smtp_configuration(session)
    if configuration is None:
        raise NotFoundError("SMTP is not configured")
    return _smtp_view(configuration)


@router.put("/smtp", response_model=SmtpConfigurationView, operation_id="platform.mail.smtp.update")
def update_smtp_configuration(
    payload: SmtpConfigurationRequest,
    session: DbSession,
    user: CurrentUserDep,
    cipher: CipherDep,
) -> SmtpConfigurationView:
    require_platform_admin(user)
    with session_transaction(session):
        configuration = service.configure_smtp(
            session,
            values=SmtpConfigurationInput(
                host=payload.host,
                port=payload.port,
                security=payload.security,
                username=payload.username,
                password=payload.password,
                from_email=payload.from_email,
                from_name=payload.from_name,
                timeout_seconds=payload.timeout_seconds,
            ),
            actor=AuditActor.from_user(user),
            cipher=cipher,
        )
    return _smtp_view(configuration)


@router.post(
    "/smtp/test",
    response_model=MailIntentView,
    status_code=status.HTTP_201_CREATED,
    operation_id="platform.mail.smtp.test",
)
def test_smtp(
    payload: TestMailRequest,
    session: DbSession,
    user: CurrentUserDep,
    cipher: CipherDep,
) -> MailIntentView:
    """Register one administrator-triggered test mail for durable delivery.

    The idempotency key is the client's operation key: retrying the same
    request replays its intent, while a new test — after a failed send, a
    configuration change or simply another attempt — carries a new key and
    starts a new delivery. The configuration revision deliberately plays no
    part: an unchanged configuration must not turn a fresh test into a stale
    replay of an older outcome.
    """
    require_platform_admin(user)
    with session_transaction(session):
        intent, _replayed = service.register_mail(
            session,
            source=PLATFORM_TEST_SOURCE,
            recipient=payload.recipient,
            subject=PLATFORM_TEST_SUBJECT,
            body=PLATFORM_TEST_BODY,
            idempotency_key=f"smtp-test:{payload.idempotency_key}",
            authorization=PlatformTestAuthorization(),
            cipher=cipher,
        )
    return _intent_view(intent)


@router.post(
    "/intents",
    response_model=MailIntentView,
    status_code=status.HTTP_201_CREATED,
    operation_id="platform.mail.intents.create",
)
def send_mail_intent(
    payload: SendMailRequest,
    session: DbSession,
    delegated: DelegatedSendDep,
    cipher: CipherDep,
) -> MailIntentView:
    """Register one delegated send; the source comes from the verified call."""
    with session_transaction(session):
        intent, _replayed = service.register_mail(
            session,
            source=delegated.source.source,
            source_ref=payload.source_ref or delegated.source.source_ref,
            recipient=payload.recipient,
            subject=payload.subject,
            body=payload.body,
            idempotency_key=payload.idempotency_key,
            authorization=delegated.authorization,
            cipher=cipher,
        )
    return _intent_view(intent)


@router.get("/intents", response_model=MailIntentPage, operation_id="platform.mail.intents.list")
def list_mail_intents(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    source: Annotated[str | None, Query(max_length=100)] = None,
) -> MailIntentPage:
    require_platform_admin(user)
    if status_filter is not None and status_filter not in MAIL_STATUSES:
        raise InvalidArgumentError("The mail status filter is unknown")
    page = PageParams(limit=limit, offset=offset)
    intents, total = service.list_intents(
        session, page=page, status=status_filter, source=source
    )
    return MailIntentPage(intents=[_intent_view(intent) for intent in intents], total=total)


@router.get(
    "/intents/{intent_id}",
    response_model=MailIntentView,
    operation_id="platform.mail.intents.read",
)
def read_mail_intent(intent_id: UUID, session: DbSession, user: CurrentUserDep) -> MailIntentView:
    require_platform_admin(user)
    return _intent_view(service.get_intent(session, intent_id))


@router.post(
    "/intents/{intent_id}/cancel",
    response_model=MailIntentView,
    operation_id="platform.mail.intents.cancel",
)
def cancel_mail_intent(
    intent_id: UUID, session: DbSession, user: CurrentUserDep
) -> MailIntentView:
    require_platform_admin(user)
    with session_transaction(session):
        intent = service.cancel_intent(
            session, intent_id=intent_id, actor=AuditActor.from_user(user)
        )
    return _intent_view(intent)


@router.post(
    "/intents/{intent_id}/reconcile",
    response_model=MailIntentView,
    operation_id="platform.mail.intents.reconcile",
)
def reconcile_mail_intent(
    intent_id: UUID,
    payload: ReconcileMailRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> MailIntentView:
    """Confirm the real outcome of one unconfirmed send; it is never re-sent."""
    require_platform_admin(user)
    if payload.outcome not in ("sent", "failed"):
        raise InvalidArgumentError("The reconciliation outcome is unknown")
    outcome: Literal["sent", "failed"] = "sent" if payload.outcome == "sent" else "failed"
    with session_transaction(session):
        intent = service.reconcile_intent(
            session,
            intent_id=intent_id,
            outcome=outcome,
            actor=AuditActor.from_user(user),
            note=payload.note,
        )
    return _intent_view(intent)
