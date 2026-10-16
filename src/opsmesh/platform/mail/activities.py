"""Mail delivery I/O: the narrow boundary that reads credentials and sends."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.platform.mail import service
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.mail import MailTransportError, SmtpSettings, send_mail
from opsmesh.shared.security.secrets import SecretCipher


@dataclass(frozen=True)
class DeliveryAttempt:
    """One claimed attempt: everything the transport needs, nothing persisted."""

    recipient: str
    subject: str
    body: str


@dataclass(frozen=True)
class DeliveryOutcome:
    """What one delivery attempt established about its mail."""

    delivered: bool = False
    outcome_unknown: bool = False
    reason: str = ""


class MailActivities:
    """Deliver registered intents; the cipher and sessions are injected."""

    def __init__(
        self, cipher: SecretCipher, sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        self._cipher = cipher
        self._sessions = sessions

    @activity.defn
    async def deliver_mail_intent(self, intent_id: UUID) -> DeliveryOutcome:
        """Send one intent; an unconfirmable outcome is never reported as sent."""

        def claim(session: Session) -> DeliveryAttempt:
            intent = service.mark_sending(session, intent_id=intent_id)
            return DeliveryAttempt(
                recipient=intent.recipient,
                subject=intent.subject,
                body=service.decrypt_intent_body(intent, self._cipher),
            )

        try:
            async with self._sessions.begin() as session:
                attempt = await session.run_sync(claim)
        except ConflictError as exc:
            if exc.code == "mail_claim_held":
                # A previous attempt may still be delivering; the workflow
                # retries after the lease instead of ending the mail.
                raise ApplicationError("The mail claim lease is still held") from None
            return DeliveryOutcome(reason=exc.code)
        except NotFoundError:
            return DeliveryOutcome(reason="absent")

        def read_settings(session: Session) -> SmtpSettings:
            configuration = service.get_smtp_configuration(session)
            if configuration is None:
                raise ApplicationError("SMTP is not configured", non_retryable=True)
            return service.smtp_settings_of(configuration, self._cipher)

        async with self._sessions.begin() as session:
            settings = await session.run_sync(read_settings)
        try:
            send_mail(
                settings,
                recipient=attempt.recipient,
                subject=attempt.subject,
                body=attempt.body,
            )
        except MailTransportError as exc:
            await self._record_failure(intent_id, exc)
            if exc.outcome_unknown:
                return DeliveryOutcome(outcome_unknown=True, reason=exc.reason)
            raise ApplicationError(
                "The mail transport refused the delivery", non_retryable=True
            ) from None
        async with self._sessions.begin() as session:
            await session.run_sync(
                lambda sync: service.mark_sent(sync, intent_id=intent_id)
            )
        return DeliveryOutcome(delivered=True)

    @activity.defn
    async def record_mail_cancellation(self, intent_id: UUID) -> None:
        """Apply a workflow cancellation to its intent, preserving uncertainty."""
        async with self._sessions.begin() as session:
            await session.run_sync(
                lambda sync: service.mark_cancelled(sync, intent_id=intent_id)
            )

    async def _record_failure(self, intent_id: UUID, error: MailTransportError) -> None:
        def record(session: Session) -> None:
            if error.outcome_unknown:
                service.mark_needs_attention(
                    session, intent_id=intent_id, reason=error.reason
                )
            else:
                service.mark_failed(session, intent_id=intent_id, reason=error.reason)

        async with self._sessions.begin() as session:
            await session.run_sync(record)
