"""Mail delivery I/O: the narrow boundary that reads credentials and sends."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.platform.mail import service
from opsmesh.platform.mail.models import MAIL_NEEDS_ATTENTION, MailIntent
from opsmesh.platform.mail.service import MailAuthorization, SourceScope
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.mail import MailTransportError, SmtpSettings, send_mail
from opsmesh.shared.security.secrets import SecretCipher


@dataclass(frozen=True)
class DeliveryAttempt:
    """One claimed attempt: everything delivery needs, nothing persisted.

    The body is not part of it: it is decrypted only after the source has
    been re-admitted for this delivery, so a revoked or rotated source never
    has its content rendered for sending.
    """

    recipient: str
    subject: str
    source: str
    source_ref: str | None


@dataclass(frozen=True)
class DeliveryOutcome:
    """What one delivery attempt established about its mail."""

    delivered: bool = False
    outcome_unknown: bool = False
    reason: str = ""


class MailActivities:
    """Deliver registered intents; the cipher, sessions and the per-source
    admission contracts are injected by bootstrap."""

    def __init__(
        self,
        cipher: SecretCipher,
        sessions: async_sessionmaker[AsyncSession],
        authorizations: dict[str, MailAuthorization] | None = None,
    ) -> None:
        self._cipher = cipher
        self._sessions = sessions
        self._authorizations = dict(authorizations or {})

    @activity.defn
    async def deliver_mail_intent(self, intent_id: UUID) -> DeliveryOutcome:
        """Send one intent; an unconfirmable outcome is never reported as sent."""

        def claim(session: Session) -> MailIntent:
            return service.claim_intent(session, intent_id=intent_id)

        try:
            async with self._sessions.begin() as session:
                claimed = await session.run_sync(claim)
        except ConflictError as exc:
            if exc.code == "mail_claim_held":
                # A previous attempt may still be delivering; the workflow
                # retries after the lease instead of ending the mail.
                raise ApplicationError("The mail claim lease is still held") from None
            return DeliveryOutcome(reason=exc.code)
        except NotFoundError:
            return DeliveryOutcome(reason="absent")

        if claimed.status == MAIL_NEEDS_ATTENTION:
            # The claim withdrew an earlier attempt whose outcome cannot be
            # confirmed; delivering again could duplicate the mail.
            return DeliveryOutcome(outcome_unknown=True, reason="attempt_uncertain")
        attempt = DeliveryAttempt(
            recipient=claimed.recipient,
            subject=claimed.subject,
            source=claimed.source,
            source_ref=claimed.source_ref,
        )

        if attempt.source not in self._authorizations:
            await self._record_failure(
                intent_id,
                MailTransportError(outcome_unknown=False, reason="source_unregistered"),
            )
            return DeliveryOutcome(reason="source_unregistered")
        # Registration-time admission does not survive a queue backlog: the
        # source is re-admitted here, under its current state, before any
        # content is decrypted or sent.
        refused = await self._admit_source(intent_id, attempt)
        if refused is not None:
            return refused

        def read_delivery(session: Session) -> tuple[SmtpSettings, str] | None:
            configuration = service.get_smtp_configuration(session)
            if configuration is None:
                return None
            settings = service.smtp_settings_of(configuration, self._cipher)
            intent = service.get_intent(session, intent_id)
            return settings, service.decrypt_intent_body(intent, self._cipher)

        async with self._sessions.begin() as session:
            delivery = await session.run_sync(read_delivery)
        if delivery is None:
            # A deterministic pre-send failure: nothing left the platform, so
            # the intent ends failed instead of stranded in sending.
            await self._record_failure(
                intent_id,
                MailTransportError(outcome_unknown=False, reason="smtp_not_configured"),
            )
            return DeliveryOutcome(reason="smtp_not_configured")
        settings, body = delivery

        try:
            # The synchronous SMTP client runs off the event loop so one slow
            # server cannot stall other activities; a timeout leaves the
            # outcome unknown because the send cannot be proven stopped.
            await asyncio.wait_for(
                asyncio.to_thread(
                    send_mail,
                    settings,
                    recipient=attempt.recipient,
                    subject=attempt.subject,
                    body=body,
                ),
                timeout=settings.timeout_seconds,
            )
        except TimeoutError:
            await self._record_failure(
                intent_id, MailTransportError(outcome_unknown=True, reason="send_timeout")
            )
            return DeliveryOutcome(outcome_unknown=True, reason="send_timeout")
        except MailTransportError as exc:
            await self._record_failure(intent_id, exc)
            if exc.outcome_unknown:
                return DeliveryOutcome(outcome_unknown=True, reason=exc.reason)
            return DeliveryOutcome(reason=exc.reason)

        async with self._sessions.begin() as session:
            await session.run_sync(lambda sync: service.mark_sent(sync, intent_id=intent_id))
        return DeliveryOutcome(delivered=True)

    @activity.defn
    async def record_mail_cancellation(self, intent_id: UUID) -> None:
        """Apply a workflow cancellation to its intent, preserving uncertainty."""
        async with self._sessions.begin() as session:
            await session.run_sync(
                lambda sync: service.mark_cancelled(sync, intent_id=intent_id)
            )

    async def _admit_source(
        self, intent_id: UUID, attempt: DeliveryAttempt
    ) -> DeliveryOutcome | None:
        """Re-admit the source for this delivery; None when it is admitted."""
        authorization = self._authorizations[attempt.source]
        scope = SourceScope(
            source=attempt.source, source_ref=attempt.source_ref, recipient=attempt.recipient
        )

        def admit(session: Session) -> None:
            authorization.authorize(session, scope)

        try:
            async with self._sessions.begin() as session:
                await session.run_sync(admit)
        except (ForbiddenError, ConflictError) as exc:
            reason = f"source_not_admitted:{exc.code}"
            await self._record_failure(
                intent_id, MailTransportError(outcome_unknown=False, reason=reason)
            )
            return DeliveryOutcome(reason=reason)
        return None

    async def _record_failure(self, intent_id: UUID, error: MailTransportError) -> None:
        def record(session: Session) -> None:
            if error.outcome_unknown:
                service.mark_needs_attention(session, intent_id=intent_id, reason=error.reason)
            else:
                service.mark_failed(session, intent_id=intent_id, reason=error.reason)

        async with self._sessions.begin() as session:
            await session.run_sync(record)
