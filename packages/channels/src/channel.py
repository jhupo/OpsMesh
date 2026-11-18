"""Connection ownership, callback admission, and the unified send contract."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Protocol, TypeVar

import anyio

from opsmesh.dispatch import DispatchResult, dispatch_event
from opsmesh.events import Event
from opsmesh.matchers import Matcher
from opsmesh.messages import Message
from opsmesh.plugins import get_current_plugin, plugin_call
from opsmesh.plugins.model import Plugin

from .callbacks import (
    EventDeduplicator,
    EventQueue,
    QueuedEvent,
    dispatch_queued,
    enqueue,
)
from .context import UserContext, bind_event_channel
from .events import MessageEvent
from .hooks import CalledHook, CallingHook, invoke_operation, register_hook

if TYPE_CHECKING:
    from opsmesh.plugins.invocation import PluginCall

    from .adapter import ChannelAdapter

CONNECTING = "connecting"
READY = "ready"
FAILED = "failed"
DISCONNECTED = "disconnected"
CLOSED = "closed"

MESSAGE_OPERATIONS = frozenset({"send_message", "stream"})


class ConnectionSettings(Protocol):
    """Options every declared connection needs before it accepts work."""

    enabled: bool
    max_pending_events: int
    concurrency: int
    shutdown_timeout: float
    dedupe_ttl: float
    dedupe_capacity: int


class InboundConnectionTask(Protocol):
    """Inbound SDK task owned by one channel connection."""

    async def run(self) -> None:
        """Run until stopped or closed by the vendor SDK."""

    def stop(self) -> None:
        """Stop accepting vendor deliveries."""

    async def wait_closed(self) -> None:
        """Wait until the SDK task has released its transport."""


class Channel:
    """Bind one declared connection to the plugin that owns it."""

    receive_types: ClassVar[frozenset[str]] = frozenset()
    send_types: ClassVar[frozenset[str]] = frozenset()
    stream_formats: ClassVar[frozenset[str]] = frozenset()
    supported_operations: ClassVar[frozenset[str]] = MESSAGE_OPERATIONS
    _calling_hook: ClassVar[set[CallingHook]] = set()
    _called_hook: ClassVar[set[CalledHook]] = set()

    def __init__(
        self,
        adapter: ChannelAdapter,
        *,
        owner: Plugin,
        connection_name: str,
        settings: Any,
    ) -> None:
        """Initialize ownership, configuration and lifecycle state."""
        self.adapter = adapter
        self.self_id = f"{adapter.get_name()}:{owner.id_}:{connection_name}"
        self._owner = owner
        self._connection_name = connection_name
        self._settings = settings
        self._state = CONNECTING
        self._replies_available = False
        self._error: str | None = None
        self._dirty = True

    def __repr__(self) -> str:
        """Identify the connection without exposing its configuration."""
        return f"Channel(type={self.type!r}, self_id={self.self_id!r})"

    def __getattr__(self, operation: str):
        """Expose operations as dynamic methods."""
        if operation.startswith("__") and operation.endswith("__"):
            raise AttributeError(operation)
        return partial(self.call, operation)

    @property
    def type(self) -> str:
        """The protocol of this connection."""
        return self.adapter.get_name()

    @property
    def owner(self) -> Plugin:
        """The plugin installation that declared this connection."""
        return self._owner

    @property
    def owner_id(self) -> str:
        """The id of the declaring plugin."""
        return self._owner.id_

    @property
    def owner_ready(self) -> bool:
        """Whether the declaring plugin is ready to accept work."""
        return self._owner.ready

    @property
    def connection_name(self) -> str:
        """The declaring plugin's name for this connection."""
        return self._connection_name

    @property
    def settings(self) -> Any:
        """The validated settings of this connection."""
        return self._settings

    @property
    def state(self) -> str:
        """The connection state."""
        return self._state

    @property
    def error(self) -> str | None:
        """A client-safe reason when the connection is not ready."""
        return self._error

    @property
    def ready(self) -> bool:
        """Whether the connection accepts inbound work."""
        return self._state == READY

    @property
    def replies_available(self) -> bool:
        """Whether admitted work may still use outbound resources."""
        return self._replies_available

    @property
    def dirty(self) -> bool:
        """Whether a rebuild must wait for successful cleanup."""
        return self._dirty

    def accepts_matcher(self, matcher: type[Matcher]) -> bool:
        """Limit dispatch to handlers owned by the declaring plugin."""
        return matcher.plugin_id == self.owner_id

    async def dispatch(
        self,
        event: Event,
        *,
        invocation_context: UserContext | None = None,
        stack: AsyncExitStack | None = None,
        matcher_filter: Callable[[type[Matcher]], bool] | None = None,
        admitted_call: PluginCall | None = None,
    ) -> DispatchResult:
        """Submit an event while retaining reply capability and owner scope."""
        if invocation_context is None and isinstance(event, MessageEvent):
            invocation_context = event.user

        def eligible(matcher: type[Matcher]) -> bool:
            return self.accepts_matcher(matcher) and (
                matcher_filter is None or matcher_filter(matcher)
            )

        with bind_event_channel(event, self):
            return await dispatch_event(
                event,
                config=self.adapter.driver.config,
                reply=partial(self.send, event),
                invocation_context=invocation_context,
                stack=stack,
                matcher_filter=eligible,
                admitted_call=admitted_call,
            )

    async def call(self, operation: str, **data: Any) -> Any:
        """Execute a protocol operation under the calling plugin's admission."""
        plugin = get_current_plugin()
        if plugin is None:
            return await invoke_operation(self, operation, data)
        async with plugin_call(plugin.id_):
            return await invoke_operation(self, operation, data)

    @classmethod
    def on_calling(cls, func: CallingHook) -> CallingHook:
        """Run a callback with the channel, operation and arguments."""
        return register_hook(cls._calling_hook, func)

    @classmethod
    def on_called(cls, func: CalledHook) -> CalledHook:
        """Observe a call's channel, error, operation, arguments and result."""
        return register_hook(cls._called_hook, func)

    async def send(
        self,
        event: Event,
        message: str | Message,
        **kwargs: Any,
    ) -> Any:
        """Send through the unified message operation of every protocol."""
        return await self.call(
            "send_message", event=event, message=message, **kwargs
        )

    async def prepare(self) -> None:
        """Acquire resources without accepting input."""

    async def start(self) -> None:
        """Open intake once the declaring plugin has finished preparing."""
        if not self._settings.enabled:
            self.mark_disconnected("connection is disabled")
            return
        self._mark_online()

    def stop(self) -> None:
        """Stop accepting input while admitted work keeps its resources."""
        self._mark_offline()

    async def drain(self) -> None:
        """Wait until admitted work has finished or been cancelled."""

    async def _release(self) -> None:
        """Release everything acquired by prepare and start."""

    async def quiesce(self) -> None:
        """Stop intake, then wait for accepted work to finish."""
        self.stop()
        await self.drain()

    async def close(self) -> None:
        """Release resources; only successful cleanup removes the connection."""
        if self._state == CLOSED and not self._dirty:
            self.adapter.forget(self)
            return
        errors: list[BaseException] = []
        try:
            self.stop()
        except BaseException as error:
            errors.append(error)
        try:
            await self.drain()
        except BaseException as error:
            errors.append(error)
        try:
            await self._release()
        except BaseException as error:
            errors.append(error)
        if errors:
            self._dirty = True
            raise BaseExceptionGroup(
                f"Connection {self.self_id} cleanup failed", errors
            )
        self._dirty = False
        self.mark_closed()
        self.adapter.forget(self)

    def mark_ready(self) -> None:
        """Mark the connection usable."""
        self._state = READY
        self._replies_available = True
        self._error = None

    def mark_failed(self, reason: str) -> None:
        """Mark the connection unusable with a safe reason."""
        self._state = FAILED
        self._error = reason

    def mark_disconnected(
        self, reason: str = "connection disconnected"
    ) -> None:
        """Mark a previously ready connection unavailable."""
        self._state = DISCONNECTED
        self._error = reason

    def mark_closed(self) -> None:
        """Mark the connection permanently closed."""
        self._state = CLOSED
        self._replies_available = False
        self._error = "connection closed"

    def _mark_online(self) -> None:
        """Publish readiness without adding a separate connection index."""
        if self._state == CLOSED:
            return
        self.mark_ready()

    def _mark_offline(self) -> None:
        """Withdraw readiness without invalidating admitted work."""
        if self._state not in {CLOSED, FAILED}:
            self.mark_disconnected()


def _event_scope(item: QueuedEvent) -> str | None:
    """Serialize callbacks only within the same conversation window."""
    event = item.event
    return event.get_session_id() if isinstance(event, MessageEvent) else None


SettingsT = TypeVar("SettingsT", bound=ConnectionSettings)


class CallbackChannel(Channel, Generic[SettingsT]):
    """A callback connection with queue, deduplication and SDK task."""

    def __init__(
        self,
        adapter: Any,
        *,
        owner: Plugin,
        connection_name: str,
        settings: SettingsT,
    ) -> None:
        """Create per-connection admission state."""
        super().__init__(
            adapter,
            owner=owner,
            connection_name=connection_name,
            settings=settings,
        )
        self._client: Any = None
        self._secret = ""
        self._inbound_task: InboundConnectionTask | None = None
        self._prepared = False
        self._dedupe = EventDeduplicator(
            ttl=settings.dedupe_ttl, capacity=settings.dedupe_capacity
        )
        self._queue = EventQueue(
            capacity=settings.max_pending_events,
            concurrency=settings.concurrency,
            shutdown_timeout=settings.shutdown_timeout,
            handle=dispatch_queued,
            scope=_event_scope,
            release=QueuedEvent.close,
        )

    @property
    def client(self) -> Any:
        """The outbound client owned by this connection."""
        if self._client is None:
            raise RuntimeError(
                f"Connection {self.self_id} has no open resources"
            )
        return self._client

    async def prepare(self) -> None:
        """Open outbound resources and callback workers."""
        if self._prepared:
            return
        if not self.settings.enabled:
            self._prepared = True
            return
        self._secret = self._read_secret()
        try:
            self._open_resources()
            await self._queue.start(self.adapter.driver.task_group)
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                try:
                    await self._release()
                except BaseException as cleanup_error:
                    raise BaseExceptionGroup(
                        "Connection preparation and cleanup failed",
                        [error, cleanup_error],
                    ) from None
            raise
        self._prepared = True

    async def start(self) -> None:
        """Start the vendor SDK task once the owner has finished preparing."""
        if not self._prepared:
            raise RuntimeError(f"Connection {self.self_id} was not prepared")
        if not self.settings.enabled:
            self.mark_disconnected("connection is disabled")
            return
        if self._inbound_task is not None:
            return
        inbound_task = self._create_inbound_task()
        self._inbound_task = inbound_task
        self.adapter.driver.task_group.start_soon(inbound_task.run)

    def stop(self) -> None:
        """Reject new callbacks and stop the vendor SDK task."""
        self._queue.stop_accepting()
        if self._inbound_task is not None:
            self._inbound_task.stop()
        self._mark_offline()

    async def drain(self) -> None:
        """Finish or cancel accepted work before outbound resources close."""
        await self._queue.stop()
        inbound_task = self._inbound_task
        if inbound_task is not None:
            await inbound_task.wait_closed()

    async def _release(self) -> None:
        """Close the SDK task, callback workers and outbound client."""
        inbound_task = self._inbound_task
        if inbound_task is not None:
            inbound_task.stop()
            await inbound_task.wait_closed()
            if self._inbound_task is inbound_task:
                self._inbound_task = None
        await self._queue.stop()
        if self._client is not None:
            await self._close_resources()
            self._client = None
        self._secret = ""
        self._prepared = False

    async def accept(self, event: Event, *, delivery_id: str) -> str:
        """Admit one verified callback without blocking the vendor callback."""
        if not self._prepared or not self.ready or not self.owner_ready:
            return "unavailable"
        return await enqueue(self._queue, self, event, delivery_id=delivery_id)

    def first_delivery(self, delivery_id: str) -> bool:
        """Record a delivery identifier and report whether it is new."""
        return not self._dedupe.seen(delivery_id)

    def release_delivery(self, delivery_id: str) -> None:
        """Allow a vendor retry when admission failed before execution."""
        self._dedupe.forget(delivery_id)

    def inbound_task_connected(self) -> None:
        """Publish readiness after the vendor SDK handshake."""
        if self._state == CLOSED:
            return
        self._mark_online()

    def inbound_task_disconnected(self) -> None:
        """Withdraw readiness after a transport loss."""
        self._mark_offline()

    def inbound_task_finished(self) -> None:
        """Release the inbound task after the SDK has closed its socket."""
        self._mark_offline()
        self._inbound_task = None

    def _read_secret(self) -> str:
        """Read the configured credential for this vendor account."""
        raise NotImplementedError

    def _open_resources(self) -> None:
        """Create the outbound client owned by this connection."""
        raise NotImplementedError

    async def _close_resources(self) -> None:
        """Close the outbound client owned by this connection."""
        raise NotImplementedError

    def _create_inbound_task(self) -> InboundConnectionTask:
        """Construct the vendor SDK task without starting it."""
        raise NotImplementedError
