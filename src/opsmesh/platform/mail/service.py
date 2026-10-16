"""Mail service: source admission, atomic registration and durable outcomes.

The service never reads user or member tables: source and recipient scope are
admitted through the injected authorization, and every terminal outcome is
written by the send Activity inside its own transaction. A send whose outcome
cannot be confirmed is never reported as delivered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.platform.mail.models import (
    MAIL_CANCELLED,
    MAIL_FAILED,
    MAIL_NEEDS_ATTENTION,
    MAIL_PENDING,
    MAIL_SECURITY_MODES,
    MAIL_SENDING,
    MAIL_SENT,
    SMTP_CONFIGURATION_ID,
    MailIntent,
    SmtpConfiguration,
)
from opsmesh.shared.errors import (
    ConflictError,
    ForbiddenError,
    InvalidArgumentError,
    NotFoundError,
)
from opsmesh.shared.mail import SmtpSettings
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.shared.serialization import payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution

MAIL_SEND_WORKFLOW = "MailSendWorkflow"
MAX_SUBJECT_LENGTH = 500
MAX_BODY_BYTES = 64 * 1024
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_SOURCE_LENGTH = 100
DEFAULT_SMTP_TIMEOUT_SECONDS = 30
DEFAULT_DAILY_SOURCE_LIMIT = 1_000
MAIL_CLAIM_LEASE_SECONDS = 300


@dataclass(frozen=True)
class SourceScope:
    """The declared origin of one send, admitted before anything is registered."""

    source: str
    source_ref: str | None
    recipient: str


class MailAuthorization(Protocol):
    """Admit one send for its source; the owner is assembled by bootstrap."""

    def authorize(self, session: Session, scope: SourceScope) -> None: ...


@dataclass(frozen=True)
class MailSource:
    """The verified origin of one delegated send."""

    source: str
    source_ref: str | None = None
    installation_id: UUID | None = None
    invocation_id: UUID | None = None


@dataclass(frozen=True)
class DelegatedMailSend:
    """One admitted delegated send: its verified source and its admission."""

    source: MailSource
    authorization: MailAuthorization


PLATFORM_TEST_SOURCE = "platform.smtp_test"
PLATFORM_TEST_SUBJECT = "OpsMesh SMTP test"
PLATFORM_TEST_BODY = "This is a test message from the OpsMesh platform mail service."


class PlatformTestAuthorization:
    """Admits only the administrator-triggered SMTP test source."""

    def authorize(self, session: Session, scope: SourceScope) -> None:
        if scope.source != PLATFORM_TEST_SOURCE:
            raise ForbiddenError("The test source is not admitted")


class MailSendExecution(BaseModel):
    """The durable command that delivers one registered intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    workflow_id: str = Field(min_length=1, max_length=255)
    workflow_name: str = MAIL_SEND_WORKFLOW
    intent_id: UUID


@dataclass(frozen=True)
class SmtpConfigurationInput:
    """The administrator-provided SMTP settings; the password stays in memory."""

    host: str
    port: int
    security: str
    username: str
    password: str
    from_email: str
    from_name: str
    timeout_seconds: int = DEFAULT_SMTP_TIMEOUT_SECONDS


def configure_smtp(
    session: Session,
    *,
    values: SmtpConfigurationInput,
    actor: AuditActor,
    cipher: SecretCipher,
) -> SmtpConfiguration:
    """Replace the single SMTP configuration; the password is stored encrypted."""
    if values.security not in MAIL_SECURITY_MODES:
        raise InvalidArgumentError(
            "The SMTP security mode is unknown",
            details={"allowed_modes": list(MAIL_SECURITY_MODES)},
        )
    if not 1 <= values.port <= 65535:
        raise InvalidArgumentError("The SMTP port is out of range")
    if values.timeout_seconds <= 0:
        raise InvalidArgumentError("The SMTP timeout must be positive")
    sealed = cipher.encrypt_payload({"password": values.password})
    configuration = session.get(SmtpConfiguration, SMTP_CONFIGURATION_ID)
    if configuration is None:
        configuration = SmtpConfiguration(id=SMTP_CONFIGURATION_ID)
        session.add(configuration)
    configuration.host = values.host
    configuration.port = values.port
    configuration.security = values.security
    configuration.username = values.username
    configuration.password_ciphertext = sealed.ciphertext
    configuration.password_key_id = sealed.key_id
    configuration.from_email = values.from_email
    configuration.from_name = values.from_name
    configuration.timeout_seconds = values.timeout_seconds
    configuration.updated_by_user_id = (
        UUID(actor.actor_id) if actor.actor_id is not None else None
    )
    configuration.updated_at = datetime.now(UTC)
    append_platform_event(
        session,
        actor=actor,
        action="platform.smtp_configured",
        target_type="smtp_configuration",
        target_id=str(SMTP_CONFIGURATION_ID),
        summary="SMTP configuration updated",
        details={"host": values.host, "port": values.port, "security": values.security},
    )
    return configuration


def get_smtp_configuration(session: Session) -> SmtpConfiguration | None:
    return session.get(SmtpConfiguration, SMTP_CONFIGURATION_ID)


def smtp_settings_of(configuration: SmtpConfiguration, cipher: SecretCipher) -> SmtpSettings:
    """Decrypt the password at the narrow boundary that actually sends."""
    if configuration.password_ciphertext is None or configuration.password_key_id is None:
        raise InvalidArgumentError("The SMTP configuration has no password")
    sealed = cipher.decrypt_payload(
        configuration.password_ciphertext, key_id=configuration.password_key_id
    )
    password = sealed.get("password")
    if not isinstance(password, str):
        raise InvalidArgumentError("The SMTP password could not be read")
    return SmtpSettings(
        host=configuration.host,
        port=configuration.port,
        security=configuration.security,
        username=configuration.username,
        password=password,
        from_email=configuration.from_email,
        from_name=configuration.from_name,
        timeout_seconds=configuration.timeout_seconds,
    )


def register_mail(
    session: Session,
    *,
    source: str,
    recipient: str,
    subject: str,
    body: str,
    idempotency_key: str,
    authorization: MailAuthorization,
    cipher: SecretCipher,
    source_ref: str | None = None,
    daily_source_limit: int = DEFAULT_DAILY_SOURCE_LIMIT,
) -> tuple[MailIntent, bool]:
    """Admit, bound and register one send intent inside the caller's transaction.

    Returns the intent and whether this call replayed an earlier registration.
    A reused key with different content is a conflict, never a second mail.
    """
    if len(source) > MAX_SOURCE_LENGTH or not source:
        raise InvalidArgumentError("The mail source is unknown")
    if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH or not idempotency_key:
        raise InvalidArgumentError("The mail idempotency key is not usable")
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise InvalidArgumentError("The mail subject is too long")
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > MAX_BODY_BYTES:
        raise InvalidArgumentError("The mail body is too long")
    scope = SourceScope(source=source, source_ref=source_ref, recipient=recipient)
    authorization.authorize(session, scope)
    content_digest = payload_digest(
        {"subject": subject, "body": body, "recipient": recipient, "source_ref": source_ref}
    )
    idempotency_scope = f"mail-source:{source}"
    existing = session.scalar(
        select(MailIntent).where(
            MailIntent.idempotency_scope == idempotency_scope,
            MailIntent.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.content_digest != content_digest:
            raise ConflictError("The idempotency key was reused with different mail content")
        return existing, True
    if daily_source_limit > 0:
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        registered_today = session.scalar(
            select(func.count())
            .select_from(MailIntent)
            .where(
                MailIntent.source == source,
                MailIntent.created_at >= day_start,
            )
        )
        if (registered_today or 0) >= daily_source_limit:
            raise ConflictError(
                "The mail source reached its daily registration limit",
                details={"source": source, "daily_limit": daily_source_limit},
            )
    sealed = cipher.encrypt_payload({"body": body})
    intent = MailIntent(
        source=source,
        source_ref=source_ref,
        recipient=recipient,
        subject=subject,
        body_ciphertext=sealed.ciphertext,
        body_key_id=sealed.key_id,
        content_digest=content_digest,
        idempotency_scope=idempotency_scope,
        idempotency_key=idempotency_key,
        status=MAIL_PENDING,
    )
    session.add(intent)
    session.flush()
    stage_execution(
        session,
        MailSendExecution(
            execution_id=intent.id,
            workflow_id=f"mail-send:{intent.id}",
            intent_id=intent.id,
        ),
    )
    return intent, False


def get_intent(session: Session, intent_id: UUID) -> MailIntent:
    intent = session.get(MailIntent, intent_id)
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    return intent


def list_intents(
    session: Session,
    *,
    page: PageParams,
    status: str | None = None,
    source: str | None = None,
) -> tuple[list[MailIntent], int]:
    """One offset page of intents plus the matching total count."""
    query = select(MailIntent)
    count_query = select(func.count()).select_from(MailIntent)
    if status is not None:
        query = query.where(MailIntent.status == status)
        count_query = count_query.where(MailIntent.status == status)
    if source is not None:
        query = query.where(MailIntent.source == source)
        count_query = count_query.where(MailIntent.source == source)
    total = session.scalar(count_query) or 0
    rows = list(
        session.scalars(
            query.order_by(MailIntent.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    )
    return rows, total


def cancel_intent(session: Session, *, intent_id: UUID, actor: AuditActor) -> MailIntent:
    """Cancel one intent that has not been sent; a sent mail cannot be recalled."""
    intent = session.get(MailIntent, intent_id)
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    if intent.status != MAIL_PENDING:
        raise ConflictError(
            "The mail intent is no longer pending", details={"status": intent.status}
        )
    intent.status = MAIL_CANCELLED
    intent.cancelled_by_user_id = UUID(actor.actor_id) if actor.actor_id else None
    append_platform_event(
        session,
        actor=actor,
        action="platform.mail_cancelled",
        target_type="mail_intent",
        target_id=str(intent.id),
        summary="Mail intent cancelled",
        details={"source": intent.source, "recipient": intent.recipient},
    )
    return intent


def reconcile_intent(
    session: Session,
    *,
    intent_id: UUID,
    outcome: Literal["sent", "failed"],
    actor: AuditActor,
    note: str | None = None,
) -> MailIntent:
    """Confirm the real outcome of one unconfirmed send; it is never re-sent."""
    intent = session.get(MailIntent, intent_id)
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    if intent.status != MAIL_NEEDS_ATTENTION:
        raise ConflictError(
            "Only an unconfirmed mail can be reconciled",
            details={"status": intent.status},
        )
    intent.status = MAIL_SENT if outcome == "sent" else MAIL_FAILED
    intent.outcome_unknown = False
    intent.reconciled_by_user_id = UUID(actor.actor_id) if actor.actor_id else None
    intent.reconciled_at = datetime.now(UTC)
    intent.reconciliation_note = note
    if outcome == "failed":
        intent.failure_reason = "reconcile_failed"
    else:
        intent.sent_at = intent.reconciled_at
    append_platform_event(
        session,
        actor=actor,
        action="platform.mail_reconciled",
        target_type="mail_intent",
        target_id=str(intent.id),
        summary=f"Mail intent reconciled as {outcome}",
        details={"source": intent.source, "note": note},
    )
    return intent


def mark_sending(session: Session, *, intent_id: UUID) -> MailIntent:
    """Claim one intent for delivery.

    A pending intent is claimed outright. An intent already claimed is only
    re-claimed once its lease expired, which bounds the window in which a
    crashed attempt could deliver twice; any other state is not claimable.
    """
    intent = session.scalar(
        select(MailIntent).where(MailIntent.id == intent_id).with_for_update()
    )
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    now = datetime.now(UTC)
    if intent.status == MAIL_PENDING or (
        intent.status == MAIL_SENDING
        and intent.claimed_at is not None
        and (now - intent.claimed_at).total_seconds() >= MAIL_CLAIM_LEASE_SECONDS
    ):
        pass
    elif intent.status == MAIL_SENDING:
        raise ConflictError(
            "The mail claim lease is still held", code="mail_claim_held"
        )
    else:
        raise ConflictError(
            "The mail intent is not claimable",
            code="mail_not_claimable",
            details={"status": intent.status},
        )
    intent.status = MAIL_SENDING
    intent.attempts += 1
    intent.claimed_at = now
    return intent


def decrypt_intent_body(intent: MailIntent, cipher: SecretCipher) -> str:
    """Decrypt one rendered body for a single delivery attempt."""
    sealed = cipher.decrypt_payload(intent.body_ciphertext, key_id=intent.body_key_id)
    body = sealed.get("body")
    if not isinstance(body, str):
        raise InvalidArgumentError("The mail body could not be read")
    return body


def mark_sent(session: Session, *, intent_id: UUID) -> MailIntent:
    intent = session.get(MailIntent, intent_id)
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    intent.status = MAIL_SENT
    intent.outcome_unknown = False
    intent.sent_at = datetime.now(UTC)
    return intent


def mark_failed(session: Session, *, intent_id: UUID, reason: str) -> MailIntent:
    intent = session.get(MailIntent, intent_id)
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    intent.status = MAIL_FAILED
    intent.outcome_unknown = False
    intent.failure_reason = reason[:200]
    return intent


def mark_needs_attention(session: Session, *, intent_id: UUID, reason: str) -> MailIntent:
    """Record an unconfirmed outcome; the operator confirms it, never a resend."""
    intent = session.get(MailIntent, intent_id)
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    intent.status = MAIL_NEEDS_ATTENTION
    intent.outcome_unknown = True
    intent.failure_reason = reason[:200]
    return intent


def mark_cancelled(session: Session, *, intent_id: UUID) -> MailIntent:
    """Cancel an intent whose workflow was cancelled before any delivery.

    An intent cancelled while its Activity was in flight has an unknown
    outcome: it moves to needs_attention for the operator to confirm, never
    to cancelled.
    """
    intent = session.get(MailIntent, intent_id)
    if intent is None:
        raise NotFoundError("The mail intent does not exist")
    if intent.status == MAIL_PENDING:
        intent.status = MAIL_CANCELLED
    elif intent.status == MAIL_SENDING:
        intent.status = MAIL_NEEDS_ATTENTION
        intent.outcome_unknown = True
        intent.failure_reason = "cancelled_during_send"
    return intent
