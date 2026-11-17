"""Robot connection ownership and lifecycle shared by native SDK adapters."""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Callable
from typing import Generic, Protocol, Self, TypeVar

import anyio

from opsmesh.adapters import Adapter, Event
from opsmesh.drivers import Driver, get_driver
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import (
    get_plugin_by_module_name,
    plugin_scope,
    track_registration,
)

from ._queue import EventQueue
from ._validation import connection_owner, connection_segment
from ._work import QueuedEvent, dispatch_queued
from .channel import CLOSED, RobotChannel, RobotProtocol

logger = logging.getLogger(__name__)
ChannelT = TypeVar("ChannelT", bound=RobotChannel)


class RobotClient(Protocol):
    """Resources kept open until all accepted callbacks have drained."""

    def open(self) -> None:
        """Open outbound resources."""

    async def close(self) -> None:
        """Close outbound resources."""


class RobotStream(Protocol):
    """Inbound SDK task, independent of outbound API resources."""

    async def run(self) -> None:
        """Run the inbound connection until stopped."""

    def stop(self) -> None:
        """Stop accepting SDK deliveries."""

    async def wait_closed(self) -> None:
        """Wait until the SDK task has closed."""


class RobotAdapter(Adapter, Generic[ChannelT]):
    """Own declarations, admission and draining; delegate vendor I/O."""

    settings_factory: Callable[[], RobotProtocol]
    channel_type: type[ChannelT]
    configuration_error: type[ValueError]
    message_event_type: type[Event]
    send_error: type[Exception]
    _client: RobotClient

    def __init__(self, driver: Driver, *, settings: RobotProtocol) -> None:
        """Initialize declarations and a bounded framework dispatch queue."""
        super().__init__(driver)
        self.settings = settings
        self._declared: dict[str, ChannelT] = {}
        self._streams: dict[ChannelT, RobotStream] = {}
        self._started = False
        self._accepting = False
        self._queue = EventQueue(
            capacity=settings.max_pending_events,
            concurrency=settings.concurrency,
            shutdown_timeout=settings.shutdown_timeout,
            handle=dispatch_queued,
            scope=lambda item: (
                item.event.get_session_id()
                if item.event.get_type() == "message"
                else None
            ),
            release=QueuedEvent.close,
        )

    @classmethod
    def configured(cls) -> Self:
        """Resolve the driver-owned adapter without opening the network."""
        driver = get_driver()
        try:
            return driver.get_adapter(cls)
        except KeyError:
            return driver.register_adapter(cls, settings=cls.settings_factory())

    @property
    def started(self) -> bool:
        """Whether outbound resources and callback workers are open."""
        return self._started

    @property
    def declarations(self) -> tuple[ChannelT, ...]:
        """Return connection declarations owned by this adapter."""
        return tuple(self._declared.values())

    def declare(self, settings, *, name: str, owner=None) -> ChannelT:
        """Bind a connection and its cleanup to the declaring plugin."""
        plugin = connection_owner(owner)
        protocol = self.get_name()
        if plugin is None:
            raise self.configuration_error(
                f"A {protocol} connection must be declared by a loaded plugin"
            )
        connection_segment(plugin.id_, "Connection owner")
        connection_segment(name, "Connection name")
        identifier = f"{protocol}:{plugin.id_}:{name}"
        if identifier in self._declared:
            raise self.configuration_error(f"Duplicate connection {identifier}")
        channel = self.channel_type(
            self,
            identifier,
            owner_id=plugin.id_,
            owner=plugin,
            connection_name=name,
            settings=settings,
            protocol=self.settings,
        )
        self._declared[identifier] = channel
        track_registration(lambda: self._remove(channel), owner)
        if isinstance(plugin, Plugin):

            async def start_connection() -> None:
                if self._started:
                    self._activate(channel)

            async def close_connection() -> None:
                stream = self._streams.get(channel)
                if stream is not None:
                    stream.stop()
                    await stream.wait_closed()
                self._deactivate(channel)

            with plugin_scope(plugin):
                self.driver.on_started(start_connection)
                self.driver.on_shutdown(close_connection)
        return channel

    async def startup(self) -> None:
        """Open outbound resources and queue workers before inbound streams."""
        if self._started:
            return
        if get_plugin_by_module_name(type(self).__module__) is not None:
            if self.settings != self.settings_factory():
                raise self.configuration_error(
                    f"{self.get_name()} protocol settings changed; "
                    "restart the host"
                )
        if not self.driver.started:
            raise self.configuration_error(
                f"The {self.get_name()} channel requires "
                "a running runtime lifespan"
            )
        self._started = True
        try:
            self._client.open()
            await self._queue.start(self.driver.task_group)
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                try:
                    await self.shutdown()
                except BaseException as cleanup_error:
                    raise BaseExceptionGroup(
                        "Robot startup and cleanup failed",
                        [error, cleanup_error],
                    ) from None
            raise

    async def start_accepting(self) -> None:
        """Start SDK intake after consumers are ready."""
        if self._accepting:
            return
        self._accepting = True
        for channel in self._declared.values():
            self._activate(channel)

    def stop_accepting(self) -> None:
        """Stop intake while preserving outbound resources for accepted work."""
        self._accepting = False
        self._queue.stop_accepting()
        for stream in tuple(self._streams.values()):
            stream.stop()

    async def drain(self) -> None:
        """Finish or cancel accepted work before closing API clients."""
        await self._queue.stop()
        for stream in tuple(self._streams.values()):
            await stream.wait_closed()

    async def shutdown(self) -> None:
        """Drain callbacks before closing outbound resources."""
        if not self._started:
            return
        self.stop_accepting()
        await self.drain()
        errors: list[Exception] = []
        for channel in self.declarations:
            try:
                self._deactivate(channel)
            except Exception as error:
                errors.append(error)
        try:
            await self._client.close()
        except Exception as error:
            errors.append(error)
        if errors:
            raise ExceptionGroup("Robot shutdown failed", errors)
        self._started = False

    def stream_finished(self, channel: ChannelT) -> None:
        """Release an inbound task after the SDK has closed its socket."""
        if channel.ready:
            self.stream_disconnected(channel)
        self._streams.pop(channel, None)

    def stream_connected(self, channel: ChannelT) -> None:
        """Publish readiness after the native SDK handshake."""
        if self._declared.get(channel.self_id) is not channel:
            return
        channel.mark_ready()
        if channel.self_id not in self.channels:
            self.connect(channel)

    def stream_disconnected(self, channel: ChannelT) -> None:
        """Withdraw inbound readiness without invalidating accepted work."""
        if self._declared.get(channel.self_id) is not channel:
            return
        if channel.self_id in self.channels:
            self.disconnect(channel)
        if channel.state != CLOSED:
            channel.mark_disconnected()

    def _remove(self, channel: ChannelT) -> None:
        self._declared.pop(channel.self_id, None)
        self._deactivate(channel)

    def _deactivate(self, channel: ChannelT) -> None:
        stream = self._streams.get(channel)
        if stream is not None:
            stream.stop()
        try:
            if channel.self_id in self.channels:
                self.disconnect(channel)
        finally:
            channel.mark_closed()
            self._forget(channel)

    def _activate(self, channel: ChannelT) -> None:
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
        except self.configuration_error as error:
            channel.mark_failed(str(error))
            logger.error("Connection %s: %s", channel.self_id, error)
            return
        stream = self._create_stream(channel, secret)
        self._streams[channel] = stream
        self.driver.task_group.start_soon(stream.run)

    def _validate_reply(self, channel: ChannelT, event: object) -> None:
        if not channel.replies_available:
            raise self.send_error(f"Connection {channel.self_id} is not ready")
        if not isinstance(event, self.message_event_type):
            raise self.send_error(
                f"Replies require a {self.message_event_type.__name__}"
            )
        if event.connection_id != channel.self_id:
            raise self.send_error("Reply event belongs to another connection")

    @abstractmethod
    def _read_secret(self, channel: ChannelT) -> str:
        """Read the configured credential for this vendor account."""

    @abstractmethod
    def _create_stream(self, channel: ChannelT, secret: str) -> RobotStream:
        """Construct the vendor SDK task without starting it."""

    @abstractmethod
    def _forget(self, channel: ChannelT) -> None:
        """Release vendor account credentials after accepted work finishes."""
