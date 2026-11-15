"""QQ Gateway adapter, event admission, and vendor operations."""

from __future__ import annotations

import logging
import os
from functools import partial
from types import ModuleType
from typing import Any

import anyio
import httpx

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
from .attachments import read as read_attachment
from .channel import QQChannel
from .client import QQClient
from .config import QQConnectionSettings, QQSettings
from .errors import (
    QQConfigurationError,
    QQError,
    QQProtocolError,
    QQSendError,
)
from .events import QQMessageEvent, parse_callback
from .groups import QQGroups
from .sending import QQSender
from .stream import QQStream

logger = logging.getLogger(__name__)


class QQAdapter(Adapter):
    """Manage QQ Bot API v2 Gateway connections for one runtime."""

    def __init__(
        self,
        driver: Driver,
        *,
        settings: QQSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the adapter without opening vendor resources."""
        super().__init__(driver)
        self.settings = settings
        self._client = QQClient(settings, http_client)
        self._declared: dict[str, QQChannel] = {}
        self._streams: dict[QQChannel, QQStream] = {}
        self._secrets: dict[str, str] = {}
        self._accepting = False
        self._queue = EventQueue(
            capacity=settings.max_pending_events,
            concurrency=settings.concurrency,
            shutdown_timeout=settings.shutdown_timeout,
            handle=self._dispatch,
            scope=lambda item: (
                item.event.get_session_id()
                if isinstance(item.event, QQMessageEvent)
                else None
            ),
            release=QueuedEvent.close,
        )
        self._started = False

    @classmethod
    def get_name(cls) -> str:
        """Return the protocol adapter name."""
        return "qq"

    @property
    def client(self) -> QQClient:
        """Return the adapter-owned HTTP API client."""
        return self._client

    @property
    def started(self) -> bool:
        """Whether the adapter owns initialized resources."""
        return self._started

    @property
    def declarations(self) -> tuple[QQChannel, ...]:
        """Return all declared QQ connections."""
        return tuple(self._declared.values())

    def declare(
        self,
        settings: QQConnectionSettings,
        *,
        name: str,
        owner: Any = None,
    ) -> QQChannel:
        """Declare a bot connection without performing network I/O."""
        plugin = self._resolve_owner(owner)
        owner_id = plugin.id_
        connection_segment(owner_id, "QQ connection owner")
        connection_segment(name, "QQ connection name")
        self_id = f"qq:{owner_id}:{name}"
        if self_id in self._declared:
            raise QQConfigurationError(f"Duplicate QQ connection {self_id}")

        channel = QQChannel(
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
            self.driver.on_shutdown(lambda: self._deactivate(channel))
        if self._started and self._accepting:
            self._activate(channel)
        return channel

    async def startup(self) -> None:
        """Open shared HTTP resources and the bounded event queue."""
        if self._started:
            return
        if get_plugin_by_module_name(__name__) is not None:
            from .config import get_settings

            if self.settings != get_settings():
                raise QQConfigurationError(
                    "Shared QQ protocol settings changed; restart the host"
                )
        if not self.driver.started:
            raise QQConfigurationError(
                "The QQ channel requires a running runtime lifespan"
            )
        self._started = True
        self._client.open()
        try:
            await self._queue.start(self.driver.task_group)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await self.shutdown()
            raise

    async def start_accepting(self) -> None:
        """Start Gateway streams after all consumer plugins are ready."""
        if self._accepting or not self.settings.enabled:
            return
        self._accepting = True
        for channel in self._declared.values():
            self._activate(channel)

    def stop_accepting(self) -> None:
        """Stop new Gateway deliveries while keeping API replies available."""
        self._accepting = False
        self._queue.stop_accepting()
        for stream in tuple(self._streams.values()):
            stream.stop()

    async def drain(self) -> None:
        """Drain accepted events and wait for Gateway tasks to finish."""
        await self._queue.stop()
        for stream in tuple(self._streams.values()):
            await stream.wait_closed()

    async def shutdown(self) -> None:
        """Stop streams, disconnect channels, and release HTTP resources."""
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
                        "Failed to disconnect QQ connection %s",
                        channel.self_id,
                    )
            channel.mark_closed()
        self._secrets.clear()
        await self._client.close()
        self._started = False

    async def _call(
        self, channel: QQChannel, operation: str, **data: Any
    ) -> Any:
        if self._declared.get(channel.self_id) is not channel:
            raise QQError("Channel does not belong to this QQ adapter")
        if operation == "stream":
            self._validate_reply(channel, data.get("event"))
            message = await aggregate_stream(data["source"])
            return await self._send_message(channel, data.get("event"), message)
        if operation == "send_message":
            return await self._send_message(
                channel, data.get("event"), data.get("message")
            )
        if operation in QQGroups.operations:
            from opsmesh.plugins.registry import get_current_plugin

            if (
                isinstance(channel._owner, Plugin)
                and get_current_plugin() is not channel._owner
            ):
                raise PermissionError("QQ connection belongs to another plugin")
            if not channel.ready:
                raise QQSendError("QQ connection is not ready")
            groups = QQGroups(self._client, channel, self._read_secret(channel))
            return await getattr(groups, operation)(**data)
        raise UnsupportedOperation(
            f"Unsupported QQ channel operation: {operation}"
        )

    def _resolve_owner(self, owner: Any) -> Plugin:
        plugin = _current_plugin.get()
        if plugin is None and owner is not None:
            module_name = (
                owner.__name__
                if isinstance(owner, ModuleType)
                else getattr(owner, "__module__", "")
            )
            plugin = get_plugin_by_module_name(module_name)
        if plugin is None:
            raise QQConfigurationError(
                "A QQ connection must be declared by a loaded plugin"
            )
        return plugin

    def _activate(self, channel: QQChannel) -> None:
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
        except QQConfigurationError as error:
            channel.mark_failed(str(error))
            logger.error("QQ connection %s: %s", channel.self_id, error)
            return
        self._secrets[channel.self_id] = secret
        stream = QQStream(channel, secret)
        self._streams[channel] = stream
        self.driver.task_group.start_soon(stream.run)

    def stream_finished(self, channel: QQChannel) -> None:
        """Release a stream after its socket and task have closed."""
        self._streams.pop(channel, None)

    def stream_connected(self, channel: QQChannel) -> None:
        """Publish the channel only after the Gateway handshake succeeds."""
        if self._declared.get(channel.self_id) is not channel:
            return
        channel.mark_ready()
        if channel.self_id not in self.channels:
            self.connect(channel)

    def stream_disconnected(self, channel: QQChannel) -> None:
        """Withdraw a channel until its Gateway reconnects."""
        if self._declared.get(channel.self_id) is not channel:
            return
        if channel.self_id in self.channels:
            self.disconnect(channel)
        if channel.state != "closed":
            channel.mark_disconnected()

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
        if not channel.first_delivery(event.event_id):
            return True
        caller = (
            UserContext(
                id=event.get_user_id(),
                name=event.user_name or event.get_user_id(),
                groups=event.group_ids,
            )
            if isinstance(event, QQMessageEvent)
            else None
        )
        try:
            item = QueuedEvent(channel, event, caller)
        except PluginUnavailable:
            channel.release_delivery(event.event_id)
            return False
        accepted = False
        try:
            accepted = self._queue.put_nowait(item)
        finally:
            if not accepted:
                channel.release_delivery(event.event_id)
                await item.close()
        if not accepted:
            logger.warning(
                "Rejecting QQ event %s for %s: queue is full",
                event.event_id,
                channel.self_id,
            )
        return accepted

    def _remove(self, channel: QQChannel) -> None:
        self._declared.pop(channel.self_id, None)
        self._deactivate(channel)

    def _deactivate(self, channel: QQChannel) -> None:
        stream = self._streams.get(channel)
        if stream is not None:
            stream.stop()
        self._secrets.pop(channel.self_id, None)
        self._client.forget(channel.self_id)
        if channel.self_id in self.channels:
            self.disconnect(channel)
        channel.mark_closed()

    def _read_secret(self, channel: QQChannel) -> str:
        reference = channel.settings.secret_ref
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise QQConfigurationError(
                f"Environment variable {reference} is not set"
            )
        return secret.strip()

    async def _dispatch(self, item: QueuedEvent) -> None:
        if item.expired or not item.channel.ready:
            logger.info(
                "Cancelled queued QQ event %s: owner stopped",
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
            logger.exception("QQ event %s dispatch failed", item.event.event_id)
            return
        if result.errors:
            logger.error(
                "QQ event %s dispatch produced %d error(s)",
                item.event.event_id,
                len(result.errors),
            )

    async def _send_message(
        self, channel: QQChannel, event: Any, message: Any
    ) -> Any:
        self._validate_reply(channel, event)
        return await QQSender(
            self._client, channel, self._secrets[channel.self_id]
        ).send(event, message)

    def _validate_reply(self, channel: QQChannel, event: Any) -> None:
        if not channel.ready:
            raise QQSendError(f"QQ connection {channel.self_id} is not ready")
        if not isinstance(event, QQMessageEvent):
            raise QQSendError("QQ replies require a QQMessageEvent")
        if event.connection_id != channel.self_id:
            raise QQSendError("QQ reply event belongs to another connection")
