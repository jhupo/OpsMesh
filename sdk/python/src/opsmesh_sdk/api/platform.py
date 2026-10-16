"""Generated platform API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``platform.mail.intents.list``
- ``platform.mail.intents.create``
- ``platform.mail.intents.read``
- ``platform.mail.intents.cancel``
- ``platform.mail.intents.reconcile``
- ``platform.mail.smtp.read``
- ``platform.mail.smtp.update``
- ``platform.mail.smtp.test``
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
)


class MailIntentView(BaseModel):
    """One durable send intent and its confirmed or unconfirmed outcome."""

    attempts: int
    created_at: datetime
    failure_reason: str | None
    intent_id: UUID
    outcome_unknown: bool
    recipient: str
    reconciled_at: datetime | None
    reconciliation_note: str | None
    sent_at: datetime | None
    source: str
    source_ref: str | None
    status: str
    subject: str


class ReconcileMailRequest(BaseModel):
    """The operator's confirmation of one unconfirmed send."""

    note: str | None = None
    outcome: str


class SendMailRequest(BaseModel):
    """One delegated send; the source comes from the verified invocation."""

    body: str
    idempotency_key: str
    recipient: str
    source_ref: str | None = None
    subject: str


class SmtpConfigurationRequest(BaseModel):
    from_email: str
    from_name: str
    host: str
    password: str
    port: int
    security: str
    timeout_seconds: int = 30
    username: str = ''


class SmtpConfigurationView(BaseModel):
    """The stored configuration; the password is never rendered back."""

    from_email: str
    from_name: str
    host: str
    password_configured: bool
    port: int
    security: str
    timeout_seconds: int
    updated_at: datetime | None
    username: str


class TestMailRequest(BaseModel):
    recipient: str


class MailIntentPage(BaseModel):
    intents: list[MailIntentView]
    total: int


class PlatformMailIntentsClient:
    """Generated client for the ``platform.mail.intents`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        source: str | None = None,
    ) -> MailIntentPage:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
                ("status", status),
                ("source", source),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/platform/mail/intents", query=query),
            self._channel.context(),
        )
        return MailIntentPage.model_validate(response.body)

    async def create(self, request: SendMailRequest) -> MailIntentView:
        """Register one delegated send; the source comes from the verified call."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path="/api/v1/platform/mail/intents",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return MailIntentView.model_validate(response.body)

    async def read(self, intent_id: UUID) -> MailIntentView:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path=f"/api/v1/platform/mail/intents/{intent_id}"),
            self._channel.context(),
        )
        return MailIntentView.model_validate(response.body)

    async def cancel(self, intent_id: UUID) -> MailIntentView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"/api/v1/platform/mail/intents/{intent_id}/cancel",
            ),
            self._channel.context(),
        )
        return MailIntentView.model_validate(response.body)

    async def reconcile(self, intent_id: UUID, request: ReconcileMailRequest) -> MailIntentView:
        """Confirm the real outcome of one unconfirmed send; it is never re-sent."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"/api/v1/platform/mail/intents/{intent_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return MailIntentView.model_validate(response.body)


class PlatformMailSmtpClient:
    """Generated client for the ``platform.mail.smtp`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self) -> SmtpConfigurationView:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/platform/mail/smtp"),
            self._channel.context(),
        )
        return SmtpConfigurationView.model_validate(response.body)

    async def update(self, request: SmtpConfigurationRequest) -> SmtpConfigurationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path="/api/v1/platform/mail/smtp",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return SmtpConfigurationView.model_validate(response.body)

    async def test(self, request: TestMailRequest) -> MailIntentView:
        """Register one administrator-triggered test mail for durable delivery."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path="/api/v1/platform/mail/smtp/test",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return MailIntentView.model_validate(response.body)


class PlatformMailClient:
    """Generated client for the ``platform.mail`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.intents = PlatformMailIntentsClient(channel)
        self.smtp = PlatformMailSmtpClient(channel)


class PlatformClient:
    """The platform API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.mail = PlatformMailClient(channel)


def create_platform_client(channel: GroupChannel) -> PlatformClient:
    """Create the platform group client over one composed platform channel."""
    return PlatformClient(channel)


__all__ = [
    "MailIntentPage",
    "MailIntentView",
    "PlatformClient",
    "PlatformMailClient",
    "PlatformMailIntentsClient",
    "PlatformMailSmtpClient",
    "ReconcileMailRequest",
    "SendMailRequest",
    "SmtpConfigurationRequest",
    "SmtpConfigurationView",
    "TestMailRequest",
    "create_platform_client",
]
