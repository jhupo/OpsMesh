"""QQ Gateway adapter, event admission, and vendor operations."""

from __future__ import annotations

import logging
import os
from functools import partial
from typing import Any

import httpx

from opsmesh.drivers import Driver
from opsmesh.plugins.channels._work import enqueue
from opsmesh.plugins.channels.context import UserContext
from opsmesh.plugins.model import Plugin

from ..adapter import RobotAdapter
from ..attachments import AttachmentCollection, AttachmentError
from ..operations import UnsupportedOperation
from ..receipts import Delivery
from ..streaming import send_stream
from .attachments import read as read_attachment
from .channel import QQChannel
from .client import QQClient
from .config import QQConnectionSettings, QQSettings, get_settings
from .errors import (
    QQConfigurationError,
    QQError,
    QQProtocolError,
    QQSendError,
)
from .events import QQMessageEvent, parse_callback
from .operations import QQOperations
from .sending import QQSender
from .stream import QQStream

logger = logging.getLogger(__name__)


class QQAdapter(RobotAdapter[QQChannel]):
    """Manage QQ Bot API v2 Gateway connections for one runtime."""

    settings_factory = staticmethod(get_settings)
    channel_type = QQChannel
    configuration_error = QQConfigurationError
    message_event_type = QQMessageEvent
    send_error = QQSendError

    def __init__(
        self,
        driver: Driver,
        *,
        settings: QQSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the adapter without opening vendor resources."""
        self._client = QQClient(settings, http_client)
        self._secrets: dict[str, str] = {}
        super().__init__(driver, settings=settings)

    @classmethod
    def get_name(cls) -> str:
        """Return the protocol adapter name."""
        return "qq"

    @property
    def client(self) -> QQClient:
        """Return the adapter-owned HTTP API client."""
        return self._client

    async def start_accepting(self) -> None:
        """Start Gateway streams after all consumer plugins are ready."""
        if self._accepting or not self.settings.enabled:
            return
        await super().start_accepting()

    async def _call(
        self, channel: QQChannel, operation: str, **data: Any
    ) -> Any:
        if self._declared.get(channel.self_id) is not channel:
            raise QQError("Channel does not belong to this QQ adapter")
        if operation == "stream":
            self._validate_reply(channel, data.get("event"))
            if "format" in data:
                raise TypeError("Stream format belongs to StreamMessageStart")
            delivery = data.get("delivery") or Delivery()
            delivery.check_owner()

            async def send(message, identifier):
                return await self._send_message(
                    channel,
                    data["event"],
                    message,
                    delivery=delivery,
                    message_id=identifier,
                )

            return await send_stream(data["source"], send)
        if operation == "send_message":
            return await self._send_message(
                channel,
                data.get("event"),
                data.get("message"),
                delivery=data.get("delivery"),
            )
        if operation in (QQOperations.operations):
            from opsmesh.plugins.registry import get_current_plugin

            if (
                isinstance(channel._owner, Plugin)
                and get_current_plugin() is not channel._owner
            ):
                raise PermissionError("QQ connection belongs to another plugin")
            if not channel.ready:
                raise QQSendError("QQ connection is not ready")
            groups = QQOperations(
                self._client, channel, self._read_secret(channel)
            )
            return await getattr(groups, operation)(**data)
        raise UnsupportedOperation(
            f"Unsupported QQ channel operation: {operation}"
        )

    async def accept_event(
        self, channel: QQChannel, payload: dict[str, Any]
    ) -> bool:
        """Parse one Gateway dispatch and admit it to the framework queue."""
        if not self._accepting or not channel.ready or not channel.owner_ready:
            return False
        try:
            event = parse_callback(
                payload,
                connection_id=channel.self_id,
                attachments=AttachmentCollection(self.settings.attachments),
                reader=partial(
                    read_attachment, settings=self.settings.attachments
                ),
            )
        except (QQProtocolError, AttachmentError):
            logger.warning("Invalid QQ event for %s", channel.self_id)
            return False
        if event is None:
            return False
        caller = (
            UserContext(
                id=event.get_user_id(),
                name=event.user_name or event.get_user_id(),
            )
            if isinstance(event, QQMessageEvent)
            else None
        )
        result = await enqueue(
            self._queue,
            channel,
            event,
            delivery_id=event.event_id,
            caller=caller,
        )
        return result in {"ok", "duplicate"}

    def _read_secret(self, channel: QQChannel) -> str:
        reference = channel.settings.secret_ref
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise QQConfigurationError(
                f"Environment variable {reference} is not set"
            )
        return secret.strip()

    async def _send_message(
        self, channel: QQChannel, event: Any, message: Any, **options
    ) -> Any:
        self._validate_reply(channel, event)
        return await QQSender(
            self._client, channel, self._secrets[channel.self_id]
        ).send(event, message, **options)

    def _forget(self, channel: QQChannel) -> None:
        self._secrets.pop(channel.self_id, None)
        self._client.forget(channel.self_id)

    def _create_stream(self, channel: QQChannel, secret: str) -> QQStream:
        self._secrets[channel.self_id] = secret
        return QQStream(channel, secret)

    def configure(
        self,
        settings: QQConnectionSettings,
        *,
        name: str,
    ) -> QQChannel:
        """Validate and declare an account from dependency configuration."""
        if (
            self.settings.enabled
            and not os.environ.get(settings.secret_ref, "").strip()
        ):
            raise ValueError(
                f"qq.{name}: missing credential environment variable "
                f"{settings.secret_ref}"
            )
        for declared in self.declarations:
            if declared.settings.app_id == settings.app_id:
                raise ValueError(
                    f"qq.{name}: robot account already belongs to "
                    f"{declared.owner_id}"
                )
        from ..lifecycle.hooks import transports

        channel = self.declare(settings, name=name)
        transports.use(self)
        return channel
