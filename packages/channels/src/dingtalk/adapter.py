"""DingTalk Stream connections, framework dispatch, and replies."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from dingtalk_stream import AckMessage, EventMessage

from opsmesh.drivers import Driver
from opsmesh.plugins.channels._work import enqueue
from opsmesh.plugins.channels.context import UserContext
from opsmesh.plugins.model import Plugin

from ..adapter import RobotAdapter
from ..attachments import AttachmentCollection, AttachmentError
from ..operations import UnsupportedOperation
from ..receipts import Delivery
from ..streaming import send_stream
from .attachments import DingTalkAttachments
from .cards import DingTalkCardStream
from .channel import DingTalkChannel
from .client import DingTalkClient
from .config import DingTalkConnectionSettings, DingTalkSettings, get_settings
from .errors import (
    DingTalkConfigurationError,
    DingTalkError,
    DingTalkProtocolError,
    DingTalkSendError,
)
from .events import DingTalkMessageEvent, DingTalkNoticeEvent, parse_callback
from .groups import DingTalkGroups
from .sending import DingTalkSender
from .stream import DingTalkStream

logger = logging.getLogger(__name__)


class DingTalkAdapter(RobotAdapter[DingTalkChannel]):
    """Manage declared DingTalk robot connections for one framework runtime."""

    settings_factory = staticmethod(get_settings)
    channel_type = DingTalkChannel
    configuration_error = DingTalkConfigurationError
    message_event_type = DingTalkMessageEvent
    send_error = DingTalkSendError

    def __init__(
        self,
        driver: Driver,
        *,
        settings: DingTalkSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the adapter without opening network resources.

        Args:
            driver: The protocol-independent framework driver.
            settings: Shared DingTalk protocol settings.
            http_client: Optional client used by tests or the host.
        """
        self._client = DingTalkClient(settings, http_client)
        super().__init__(driver, settings=settings)

    @classmethod
    def get_name(cls) -> str:
        """Return the adapter name used by channel types."""
        return "dingtalk"

    async def _call(
        self, channel: DingTalkChannel, operation: str, **data: Any
    ) -> Any:
        if self._declared.get(channel.self_id) is not channel:
            raise DingTalkError(
                "Channel does not belong to this DingTalk adapter"
            )
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

            async def card(block, identifier):
                return await DingTalkCardStream(self._client, channel).send(
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
                data.get("event"),
                data.get("message"),
                delivery=data.get("delivery"),
            )
        if operation in DingTalkGroups.operations:
            from opsmesh.plugins.registry import get_current_plugin

            if (
                isinstance(channel._owner, Plugin)
                and get_current_plugin() is not channel._owner
            ):
                raise PermissionError(
                    "DingTalk connection belongs to another plugin"
                )
            if not channel.ready:
                raise DingTalkSendError("DingTalk connection is not ready")
            groups = DingTalkGroups(self._client, channel)
            return await getattr(groups, operation)(**data)
        raise UnsupportedOperation(
            f"Unsupported DingTalk channel operation: {operation}"
        )

    @property
    def client(self) -> DingTalkClient:
        """Return the adapter-owned asynchronous vendor transport."""
        return self._client

    def _read_secret(self, channel: DingTalkChannel) -> str:
        reference = channel.settings.client_secret_ref
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise DingTalkConfigurationError(
                f"Environment variable {reference} is not set"
            )
        return secret.strip()

    async def accept_callback(
        self, channel: DingTalkChannel, payload: dict[str, Any]
    ) -> tuple[int, str]:
        """Admit a verified SDK callback and return a native ACK status."""
        if not self._accepting or not channel.ready or not channel.owner_ready:
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "connection unavailable"
        if not isinstance(payload, dict):
            return AckMessage.STATUS_BAD_REQUEST, "invalid payload"
        try:
            event = parse_callback(
                payload,
                connection_id=channel.self_id,
                attachments=AttachmentCollection(self.settings.attachments),
                reader=DingTalkAttachments(
                    self._client, channel.settings, self.settings.attachments
                ).read,
            )
        except (DingTalkProtocolError, AttachmentError):
            return AckMessage.STATUS_BAD_REQUEST, "invalid payload"
        if event is None:
            return AckMessage.STATUS_OK, "ignored"
        caller = UserContext(
            id=event.get_user_id(),
            name=event.user_name or event.get_user_id(),
        )
        result = await enqueue(
            self._queue,
            channel,
            event,
            delivery_id=event.message_id,
            caller=caller,
        )
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
        if not self._accepting or not channel.ready or not channel.owner_ready:
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
        result = await enqueue(
            self._queue, channel, event, delivery_id=event_id
        )
        return (
            AckMessage.STATUS_OK
            if result in {"ok", "duplicate"}
            else AckMessage.STATUS_SYSTEM_EXCEPTION,
            result,
        )

    async def _send_message(
        self, channel: DingTalkChannel, event: Any, message: Any, **options
    ) -> Any:
        self._validate_reply(channel, event)
        return await DingTalkSender(self._client, channel).send(
            event, message, **options
        )

    def _forget(self, channel: DingTalkChannel) -> None:
        self._client.forget(channel.settings)

    def _create_stream(
        self, channel: DingTalkChannel, secret: str
    ) -> DingTalkStream:
        return DingTalkStream(channel, secret)

    def configure(
        self,
        settings: DingTalkConnectionSettings,
        *,
        name: str,
    ) -> DingTalkChannel:
        """Validate and declare an account from dependency configuration."""
        if not os.environ.get(settings.client_secret_ref, "").strip():
            raise ValueError(
                f"dingtalk.{name}: missing credential environment variable "
                f"{settings.client_secret_ref}"
            )
        for declared in self.declarations:
            if declared.settings.client_id == settings.client_id:
                raise ValueError(
                    f"dingtalk.{name}: robot account already belongs to "
                    f"{declared.owner_id}"
                )
        from ..lifecycle.hooks import transports

        channel = self.declare(settings, name=name)
        transports.use(self)
        return channel
