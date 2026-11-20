"""Driver-owned DingTalk adapter and protocol dispatch."""
import os
from typing import Any
from dingtalk_stream import AckMessage, EventMessage
from opsmesh.adapters import Adapter as BaseAdapter
from opsmesh.drivers import Driver
from .attachments import AttachmentCollection, AttachmentError
from .config import Config, Settings
from .receipts import Delivery
from .streaming import send_stream
from .gateway import Gateway
from .protocol.attachments import DingTalkAttachments
from .protocol.cards import DingTalkCardStream
from .protocol.config import DingTalkConnectionSettings
from .protocol.errors import DingTalkProtocolError, DingTalkSendError
from .protocol.events import DingTalkMessageEvent, DingTalkNoticeEvent, parse_callback
from .protocol.groups import DingTalkGroups
from .protocol.sending import DingTalkSender

class Adapter(BaseAdapter):
    @classmethod
    def get_name(cls):
        return "dingtalk"

    @staticmethod
    def _read_secret(account: DingTalkConnectionSettings) -> str:
        secret = os.environ.get(account.client_secret_ref, "").strip()
        if not secret:
            raise ValueError(f"Missing DingTalk secret: {account.client_secret_ref}")
        return secret

    @classmethod
    def prepare_settings(
        cls,
        driver: Driver,
        config: Config,
        bindings: dict[str, DingTalkConnectionSettings],
    ) -> dict[str, Any]:
        accounts = list(config.dingtalk.accounts)
        ids = {account.client_id for account in accounts}
        for account in accounts:
            if account.enabled:
                cls._read_secret(account)
        for owner, account in bindings.items():
            path = f"{owner}.dependencies.dingtalk"
            if account.client_id in ids:
                raise ValueError(f"{path}: duplicate DingTalk client_id {account.client_id}")
            if account.enabled:
                try:
                    cls._read_secret(account)
                except ValueError as exc:
                    raise ValueError(f"{path}: {exc}") from None
            ids.add(account.client_id)
            accounts.append(account)
        return {"settings": Settings(accounts=accounts), "bindings": bindings}

    def __init__(
        self,
        driver: Driver,
        *,
        settings: Settings,
        bindings: dict[str, DingTalkConnectionSettings] | None = None,
    ):
        super().__init__(driver)
        owners = {account.client_id: owner for owner, account in (bindings or {}).items()}
        by_id = {
            a.client_id: Gateway(self, a, owner_plugin_id=owners.get(a.client_id))
            for a in settings.accounts
            if a.enabled
        }
        self.accounts = list(by_id.values())
        self.connections = {
            key: by_id[account.client_id]
            for key, account in (bindings or {}).items()
            if account.enabled
        }
        self.on_ready(self.startup)
        driver.on_shutdown(self.shutdown)

    async def startup(self):
        try:
            for gateway in self.accounts:
                await gateway.open(self._read_secret(gateway.settings))
        except BaseException:
            await self.shutdown()
            raise

    async def shutdown(self):
        for gateway in reversed(self.accounts):
            await gateway.close()

    async def _call_api(
        self, channel: Gateway, api: str, **data: Any
    ) -> Any:
        operation = api
        if operation in {"stream", "send_message"}:
            event = data.get("event")
            if not isinstance(event, DingTalkMessageEvent) or event.connection_id != channel.id:
                raise DingTalkSendError("Cannot reply outside the originating gateway")
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
                f"Connection {channel.id} is not ready"
            )
        if operation not in DingTalkGroups.operations:
            raise ValueError("Unknown DingTalk API")
        return await getattr(DingTalkGroups(channel), operation)(**data)

    async def accept_callback(
        self, channel: Gateway, payload: dict[str, Any]
    ) -> tuple[int, str]:
        """Admit a verified SDK callback and return a native ACK status."""
        if not channel.ready:
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "connection unavailable"
        if not isinstance(payload, dict):
            return AckMessage.STATUS_BAD_REQUEST, "invalid payload"
        settings = channel.settings.attachments
        try:
            event = parse_callback(
                payload,
                connection_id=channel.id,
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
        self, channel: Gateway, message: EventMessage
    ) -> tuple[int, str]:
        """Admit a native EVENT after the authenticated Stream delivered it."""
        if not channel.ready:
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
            connection_id=channel.id,
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
        self, channel: Gateway, event: Any, message: Any, **options: Any
    ) -> Any:
        return await DingTalkSender(channel).send(event, message, **options)
