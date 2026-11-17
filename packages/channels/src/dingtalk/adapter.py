"""DingTalk Stream connections, framework dispatch, and replies."""

from __future__ import annotations

import logging
from typing import Any

from dingtalk_stream import AckMessage, EventMessage

from ..adapter import ChannelAdapter
from ..attachments import AttachmentCollection, AttachmentError
from ..receipts import Delivery
from ..streaming import send_stream
from .attachments import DingTalkAttachments
from .cards import DingTalkCardStream
from .channel import DingTalkChannel
from .config import DingTalkConnectionSettings
from .errors import (
    DingTalkConfigurationError,
    DingTalkProtocolError,
    DingTalkSendError,
)
from .events import DingTalkMessageEvent, DingTalkNoticeEvent, parse_callback
from .groups import DingTalkGroups
from .sending import DingTalkSender

logger = logging.getLogger(__name__)


class DingTalkAdapter(ChannelAdapter[DingTalkChannel]):
    """Create DingTalk robot connections and implement their operations."""

    channel_type = DingTalkChannel
    configuration_error = DingTalkConfigurationError
    message_event_type = DingTalkMessageEvent
    send_error = DingTalkSendError

    @classmethod
    def get_name(cls) -> str:
        """Return the adapter name used by channel types."""
        return "dingtalk"

    def _account_id(self, settings: DingTalkConnectionSettings) -> str:
        return settings.client_id

    async def _perform(
        self, channel: DingTalkChannel, operation: str, **data: Any
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

            async def card(block: Any, identifier: str) -> Any:
                return await DingTalkCardStream(channel).send(
                    data["event"],
                    block.chunks(),
                    delivery=delivery,
                    message_id=identifier,
                )

            return await send_stream(
                data["source"],
                send,
                card=card if channel.settings.card else None,
            )
        if operation == "send_message":
            return await self._send_message(
                channel,
                data["event"],
                data["message"],
                delivery=data.get("delivery"),
            )
        if not channel.ready:
            raise DingTalkSendError(
                f"Connection {channel.self_id} is not ready"
            )
        return await getattr(DingTalkGroups(channel), operation)(**data)

    async def accept_callback(
        self, channel: DingTalkChannel, payload: dict[str, Any]
    ) -> tuple[int, str]:
        """Admit a verified SDK callback and return a native ACK status."""
        if not channel.ready or not channel.owner_ready:
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "connection unavailable"
        if not isinstance(payload, dict):
            return AckMessage.STATUS_BAD_REQUEST, "invalid payload"
        settings = channel.settings.attachments
        try:
            event = parse_callback(
                payload,
                connection_id=channel.self_id,
                attachments=AttachmentCollection(settings),
                reader=DingTalkAttachments(channel.client, settings).read,
            )
        except (DingTalkProtocolError, AttachmentError):
            return AckMessage.STATUS_BAD_REQUEST, "invalid payload"
        if event is None:
            return AckMessage.STATUS_OK, "ignored"
        result = await channel.accept(event, delivery_id=event.message_id)
        return (
            AckMessage.STATUS_OK
            if result in {"ok", "duplicate"}
            else AckMessage.STATUS_SYSTEM_EXCEPTION,
            result,
        )

    async def accept_notice(
        self, channel: DingTalkChannel, message: EventMessage
    ) -> tuple[int, str]:
        """Admit a native EVENT after the authenticated Stream delivered it."""
        if not channel.ready or not channel.owner_ready:
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "connection unavailable"
        headers = message.headers
        event_id, event_type = (
            headers.event_id or headers.message_id,
            headers.event_type,
        )
        if (
            not isinstance(event_id, str)
            or not isinstance(event_type, str)
            or not isinstance(message.data, dict)
        ):
            return AckMessage.STATUS_BAD_REQUEST, "invalid event"
        data = message.data
        actor = data.get("operator")
        open_conversation = data.get("openConversationId")
        chat = data.get("chatId")
        event = DingTalkNoticeEvent(
            connection_id=channel.self_id,
            event_id=event_id,
            event_type=event_type,
            open_conversation_id=open_conversation
            if isinstance(open_conversation, str)
            else None,
            chat_id=chat if isinstance(chat, str) else None,
            operator_id=actor if isinstance(actor, str) else None,
            raw=data,
        )
        result = await channel.accept(event, delivery_id=event_id)
        return (
            AckMessage.STATUS_OK
            if result in {"ok", "duplicate"}
            else AckMessage.STATUS_SYSTEM_EXCEPTION,
            result,
        )

    async def _send_message(
        self, channel: DingTalkChannel, event: Any, message: Any, **options: Any
    ) -> Any:
        return await DingTalkSender(channel).send(event, message, **options)
