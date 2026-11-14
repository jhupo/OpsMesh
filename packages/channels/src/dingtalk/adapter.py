"""DingTalk Stream connections, framework dispatch, and replies."""

from __future__ import annotations

import logging
import os
from types import ModuleType
from typing import Any

import anyio
import httpx
from dingtalk_stream import AckMessage, EventMessage

from opsmesh.adapters import Adapter
from opsmesh.dispatch import dispatch_event
from opsmesh.drivers import Driver
from opsmesh.plugins.channels._queue import EventQueue
from opsmesh.plugins.channels._validation import connection_segment
from opsmesh.plugins.channels._work import QueuedEvent
from opsmesh.plugins.channels.context import UserContext
from opsmesh.plugins.invocation import PluginUnavailable, invocation_scope
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import (
    _current_plugin,
    get_plugin_by_module_name,
    track_registration,
)

from ..attachments import AttachmentCollection, AttachmentError
from ..operations import UnsupportedOperation
from ..sending import aggregate_stream
from .attachments import DingTalkAttachments
from .channel import DingTalkChannel
from .client import DingTalkClient
from .config import DingTalkConnectionSettings, DingTalkSettings
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


class DingTalkAdapter(Adapter):
    """Manage declared DingTalk robot connections for one framework runtime."""

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
        super().__init__(driver)
        self.settings = settings
        self._client = DingTalkClient(settings, http_client)
        self._declared: dict[str, DingTalkChannel] = {}
        self._streams: dict[DingTalkChannel, DingTalkStream] = {}
        self._accepting = False
        self._queue = EventQueue(
            capacity=settings.max_pending_events,
            concurrency=settings.concurrency,
            shutdown_timeout=settings.shutdown_timeout,
            handle=self._dispatch,
            scope=lambda item: (
                item.event.get_session_id()
                if isinstance(item.event, DingTalkMessageEvent)
                else None
            ),
            release=QueuedEvent.close,
        )
        self._started = False

    @classmethod
    def get_name(cls) -> str:
        """Return the adapter name used by channel types."""
        return "dingtalk"

    @property
    def started(self) -> bool:
        """Whether callbacks are being served."""
        return self._started

    @property
    def declarations(self) -> tuple[DingTalkChannel, ...]:
        """Return a snapshot of declared connections."""
        return tuple(self._declared.values())

    def declare(
        self,
        settings: DingTalkConnectionSettings,
        *,
        name: str,
        owner: Any = None,
    ) -> DingTalkChannel:
        """Declare a robot connection owned by the calling plugin.

        Declaration validates and namespaces the connection but performs no
        network I/O. The connection opens during the adapter startup.
        """
        plugin = self._resolve_owner(owner)
        owner_id = plugin.id_
        connection_segment(owner_id, "DingTalk connection owner")
        connection_segment(name, "DingTalk connection name")
        self_id = f"dingtalk:{owner_id}:{name}"
        if self_id in self._declared:
            raise DingTalkConfigurationError(
                f"Duplicate DingTalk connection {self_id}"
            )

        channel = DingTalkChannel(
            self,
            self_id,
            owner_id=owner_id,
            owner=plugin,
            connection_name=name,
            settings=settings,
            protocol=self.settings,
        )
        self._declared[self_id] = channel
        track_registration(lambda: self._remove(channel), owner)
        if isinstance(plugin, Plugin):
            self.driver.on_ready(
                lambda: self._activate(channel) if self._started else None
            )

            async def close_connection() -> None:
                stream = self._streams.get(channel)
                if stream is not None:
                    stream.stop()
                    await stream.wait_closed()
                if channel.self_id in self.channels:
                    self.disconnect(channel)
                channel.mark_closed()

            self.driver.on_shutdown(close_connection)
        if self._started:
            self._activate(channel)
        return channel

    async def startup(self) -> None:
        """Open shared resources and activate declared connections."""
        if self._started:
            return
        if get_plugin_by_module_name(__name__) is not None:
            from .config import get_settings

            if self.settings != get_settings():
                raise DingTalkConfigurationError(
                    "DingTalk protocol settings changed; restart the host"
                )
        if not self.driver.started:
            raise DingTalkConfigurationError(
                "The DingTalk channel requires a running runtime lifespan"
            )
        self._started = True
        self._client.open()
        try:
            await self._queue.start(self.driver.task_group)
            for channel in list(self._declared.values()):
                self._activate(channel)
        except BaseException:
            with anyio.CancelScope(shield=True):
                try:
                    await self.shutdown()
                except BaseException:
                    logger.exception(
                        "Failed to roll back DingTalk adapter startup"
                    )
            raise

    def stop_accepting(self) -> None:
        """Stop inbound callbacks while keeping reply resources available."""
        self._queue.stop_accepting()
        self._accepting = False
        for stream in tuple(self._streams.values()):
            stream.stop()

    async def drain(self) -> None:
        """Finish accepted events before consumer plugins close."""
        await self._queue.stop()
        for stream in tuple(self._streams.values()):
            await stream.wait_closed()

    async def shutdown(self) -> None:
        """Stop callbacks, drain queued events, and release resources."""
        if not self._started:
            return
        self.stop_accepting()
        await self.drain()
        for channel in list(self._declared.values()):
            if channel.self_id in self.channels:
                try:
                    self.disconnect(channel)
                except Exception:
                    logger.exception(
                        "Failed to disconnect DingTalk connection %s",
                        channel.self_id,
                    )
            channel.mark_closed()
        self._streams.clear()
        await self._client.close()
        self._started = False

    async def _call(
        self, channel: DingTalkChannel, operation: str, **data: Any
    ) -> Any:
        if self._declared.get(channel.self_id) is not channel:
            raise DingTalkError(
                "Channel does not belong to this DingTalk adapter"
            )
        if operation == "stream":
            self._validate_reply(channel, data.get("event"))
            message = await aggregate_stream(data["source"])
            return await self._send_message(channel, data.get("event"), message)
        if operation == "send_message":
            return await self._send_message(
                channel, data.get("event"), data.get("message")
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

    def _resolve_owner(self, owner: Any):
        plugin = _current_plugin.get()
        if plugin is None and owner is not None:
            module_name = (
                owner.__name__
                if isinstance(owner, ModuleType)
                else getattr(owner, "__module__", "")
            )
            plugin = get_plugin_by_module_name(module_name)
        if plugin is None:
            raise DingTalkConfigurationError(
                "A DingTalk connection must be declared by a loaded plugin"
            )
        return plugin

    @property
    def client(self) -> DingTalkClient:
        """Return the adapter-owned asynchronous vendor transport."""
        return self._client

    async def start_accepting(self) -> None:
        """Open streams only after all consumer plugins become ready."""
        if self._accepting:
            return
        self._accepting = True
        for channel in self._declared.values():
            self._activate(channel)

    def _activate(self, channel: DingTalkChannel) -> None:
        if not self._accepting or channel in self._streams:
            return
        owner = channel._owner
        if isinstance(owner, Plugin) and owner.status not in (
            PluginStatus.STARTING,
            PluginStatus.READY,
        ):
            return
        try:
            secret = self._read_secret(channel)
        except DingTalkConfigurationError as error:
            channel.mark_failed(str(error))
            logger.error("DingTalk connection %s: %s", channel.self_id, error)
            return
        stream = DingTalkStream(channel, secret)
        self._streams[channel] = stream
        self.driver.task_group.start_soon(stream.run)

    def stream_finished(self, channel: DingTalkChannel) -> None:
        """Release a stopped connection task after its resources close."""
        self._streams.pop(channel, None)

    def stream_connected(self, channel: DingTalkChannel) -> None:
        """Publish a channel only after its stream handshake succeeds."""
        if self._declared.get(channel.self_id) is not channel:
            return
        channel.mark_ready()
        if channel.self_id not in self.channels:
            self.connect(channel)

    def stream_disconnected(self, channel: DingTalkChannel) -> None:
        """Withdraw disconnected streams until a reconnect succeeds."""
        if self._declared.get(channel.self_id) is not channel:
            return
        if channel.self_id in self.channels:
            self.disconnect(channel)
        channel.mark_disconnected()

    def _remove(self, channel: DingTalkChannel) -> None:
        self._declared.pop(channel.self_id, None)
        stream = self._streams.get(channel)
        if stream is not None:
            stream.stop()
        if channel.self_id in self.channels:
            self.disconnect(channel)
        channel.mark_closed()

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
        if not channel.first_delivery(event.message_id):
            return AckMessage.STATUS_OK, "duplicate"

        caller = UserContext(
            id=event.get_user_id(),
            name=event.user_name or event.get_user_id(),
            groups=event.group_ids,
        )
        try:
            item = QueuedEvent(channel, event, caller)
        except PluginUnavailable:
            channel.release_delivery(event.message_id)
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "plugin unavailable"
        accepted = False
        try:
            accepted = self._queue.put_nowait(item)
        finally:
            if not accepted:
                channel.release_delivery(event.message_id)
                await item.close()
        if not accepted:
            logger.warning(
                "Rejecting DingTalk event %s for %s: queue is full",
                event.message_id,
                channel.self_id,
            )
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "queue is full"
        return AckMessage.STATUS_OK, "ok"

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
        if not channel.first_delivery(event_id):
            return AckMessage.STATUS_OK, "duplicate"
        try:
            item = QueuedEvent(channel, event, None)
        except PluginUnavailable:
            channel.release_delivery(event_id)
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "plugin unavailable"
        accepted = False
        try:
            accepted = self._queue.put_nowait(item)
        finally:
            if not accepted:
                channel.release_delivery(event_id)
                await item.close()
        if not accepted:
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "queue is full"
        return AckMessage.STATUS_OK, "ok"

    async def _dispatch(self, item: QueuedEvent) -> None:
        if item.expired or not item.channel.ready:
            logger.info(
                "Cancelled queued DingTalk event %s: owner stopped",
                item.event.message_id,
            )
            return
        try:
            with invocation_scope(item.admitted_call):
                result = await dispatch_event(
                    item.channel,
                    item.event,
                    invocation_context=item.caller,
                    matcher_filter=item.channel.accepts_matcher,
                    admitted_call=item.admitted_call,
                )
        except Exception:
            logger.exception(
                "DingTalk event %s dispatch failed", item.event.message_id
            )
            return
        if result.errors:
            logger.error(
                "DingTalk event %s dispatch produced %d error(s)",
                item.event.message_id,
                len(result.errors),
            )

    async def _send_message(
        self, channel: DingTalkChannel, event: Any, message: Any
    ) -> Any:
        self._validate_reply(channel, event)
        return await DingTalkSender(self._client, channel).send(event, message)

    def _validate_reply(self, channel: DingTalkChannel, event: Any) -> None:
        if not channel.ready:
            raise DingTalkSendError(
                f"DingTalk connection {channel.self_id} is not ready"
            )
        if not isinstance(event, DingTalkMessageEvent):
            raise DingTalkSendError(
                "DingTalk replies require a DingTalkMessageEvent"
            )
        if event.connection_id != channel.self_id:
            raise DingTalkSendError(
                "DingTalk reply event belongs to another connection"
            )
