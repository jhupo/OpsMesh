"""Stateless SMTP sending with explicit delivery certainty.

This module never creates mail tasks or decides recipients; callers own the
rendered content, the authorized recipient range and the delivery intent.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    username: str
    password: str = field(repr=False)
    from_email: str
    from_name: str
    timeout_seconds: float


class MailTransportError(Exception):
    """Raised when delivery failed or its outcome cannot be confirmed."""

    def __init__(self, *, outcome_unknown: bool, reason: str) -> None:
        super().__init__(reason)
        self.outcome_unknown = outcome_unknown
        self.reason = reason


def send_mail(settings: SmtpSettings, *, recipient: str, subject: str, body: str) -> None:
    """Send one rendered message; unknown outcomes are never reported as sent."""
    message = EmailMessage()
    message["From"] = formataddr((settings.from_name, settings.from_email))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if settings.security not in {"tls", "starttls"}:
        raise MailTransportError(outcome_unknown=False, reason="invalid_security")
    context = ssl.create_default_context()
    sending = False
    accepted = False
    try:
        client = (
            smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=settings.timeout_seconds, context=context
            )
            if settings.security == "tls"
            else smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout_seconds)
        )
        with client:
            if settings.security == "starttls":
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            if settings.username:
                client.login(settings.username, settings.password)
            sending = True
            refused = client.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
            # QUIT failure cannot erase a confirmed DATA acknowledgement.
            sending = False
            accepted = True
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise MailTransportError(outcome_unknown=False, reason="smtp_rejected") from None
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        if accepted:
            return
        raise MailTransportError(outcome_unknown=sending, reason=type(exc).__name__) from None
