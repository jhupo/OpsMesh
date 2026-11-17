"""QQ Gateway adapter, event admission, and vendor operations."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from ..adapter import ChannelAdapter
from ..attachments import AttachmentCollection, AttachmentError
from ..receipts import Delivery
from ..streaming import send_stream
from .attachments import read as read_attachment
from .channel import QQChannel
from .config import QQConnectionSettings
from .errors import QQConfigurationError, QQProtocolError, QQSendError
from .events import QQMessageEvent, parse_callback
from .operations import QQOperations
from .sending import QQSender

logger = logging.getLogger(__name__)


class QQAdapter(ChannelAdapter[QQChannel]):
    """Create QQ bot connections and implement their operations."""

    channel_type = QQChannel
    configuration_error = QQConfigurationError
    message_event_type = QQMessageEvent
    send_error = QQSendError

    @classmethod
    def get_name(cls) -> str:
        """Return the protocol adapter name."""
        return "qq"

    def _account_id(self, settings: QQConnectionSettings) -> str:
        return settings.app_id

    async def _perform(
        self, channel: QQChannel, operation: str, **data: Any
    ) -> Any:
        if operation == "stream":
            if "format" in data:
                raise TypeError("Stream format belongs to StreamMessageStart")
            delivery = data.get("delivery") or Delivery()
            delivery.check_owner()

            async def send(message: Any, identifier: str) -> Any:
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
                data["event"],
                data["message"],
                delivery=data.get("delivery"),
            )
        if not channel.ready:
            raise QQSendError(f"Connection {channel.self_id} is not ready")
        return await getattr(QQOperations(channel), operation)(**data)

    async def accept_event(
        self, channel: QQChannel, payload: dict[str, Any]
    ) -> bool:
        """Parse one Gateway dispatch and admit it to the framework queue."""
        if not channel.ready or not channel.owner_ready:
            return False
        settings = channel.settings.attachments
        try:
            event = parse_callback(
                payload,
                connection_id=channel.self_id,
                attachments=AttachmentCollection(settings),
                reader=partial(read_attachment, settings=settings),
            )
        except (QQProtocolError, AttachmentError):
            logger.warning("Invalid QQ event for %s", channel.self_id)
            return False
        if event is None:
            return False
        result = await channel.accept(event, delivery_id=event.event_id)
        return result in {"ok", "duplicate"}

    async def _send_message(
        self, channel: QQChannel, event: Any, message: Any, **options: Any
    ) -> Any:
        return await QQSender(channel).send(event, message, **options)
