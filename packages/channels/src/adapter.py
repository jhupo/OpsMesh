"""Prepared connections and the shared operation contract of one protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from opsmesh.drivers import Driver
from opsmesh.plugins import get_current_plugin
from opsmesh.plugins.model import Plugin
from opsmesh.plugins.registry import plugin_scope, track_registration

from .channel import MESSAGE_OPERATIONS, Channel
from .config import validate_connection_name
from .events import MessageEvent
from .operations import UnsupportedOperation

ChannelT = TypeVar("ChannelT", bound=Channel)


class ChannelAdapter(ABC, Generic[ChannelT]):
    """Create connections for one protocol and authorize their operations.

    The prepared connection table is the sole index for one protocol.
    """

    channel_type: type[ChannelT]
    configuration_error: type[Exception] = ValueError
    send_error: type[Exception] = Exception
    message_event_type: type[MessageEvent]

    def __init__(self, driver: Driver) -> None:
        """Initialize the prepared connection table."""
        self.driver = driver
        self._connections: dict[tuple[str, str], ChannelT] = {}

    @classmethod
    @abstractmethod
    def get_name(cls) -> str:
        """Return the protocol identifier."""
        raise NotImplementedError

    @property
    def connections(self) -> tuple[ChannelT, ...]:
        """The connections prepared by this adapter."""
        return tuple(self._connections.values())

    def get_connection(self, owner_id: str, name: str = "main") -> ChannelT:
        """Return a prepared connection without starting or creating it."""
        try:
            return self._connections[(owner_id, name)]
        except KeyError:
            raise LookupError(
                f"{self.get_name()} connection {owner_id}/{name} "
                "is not prepared"
            ) from None

    def declare(self, settings: Any, *, name: str = "main") -> ChannelT:
        """Bind a configured connection and its cleanup to the caller."""
        owner = get_current_plugin()
        if not isinstance(owner, Plugin):
            raise self.configuration_error(
                f"A {self.get_name()} connection must be declared by "
                "a loaded plugin"
            )
        validate_connection_name(name)
        key = (owner.id_, name)
        existing = self._connections.get(key)
        if existing is not None:
            if existing.dirty:
                raise self.configuration_error(
                    f"Connection {existing.self_id} requires successful "
                    "cleanup before it can be rebuilt"
                )
            raise self.configuration_error(
                f"Duplicate connection {existing.self_id}"
            )
        account = self._account_id(settings)
        if account is not None:
            for other in self._connections.values():
                if self._account_id(other.settings) == account:
                    raise self.configuration_error(
                        f"{self.get_name()}.{name}: the robot account is "
                        f"already declared by {other.owner_id}"
                    )
        channel = self.channel_type(
            self, owner=owner, connection_name=name, settings=settings
        )
        self._connections[key] = channel
        with plugin_scope(owner):
            self.driver.on_started(channel.start)
            self.driver.on_quiescing(channel.quiesce)
            self.driver.on_shutdown(channel.close)
            track_registration(lambda: self.forget(channel))
        return channel

    def forget(self, channel: ChannelT) -> None:
        """Remove a prepared connection after successful cleanup."""
        key = (channel.owner_id, channel.connection_name)
        if self._connections.get(key) is channel:
            del self._connections[key]

    def _account_id(self, settings: Any) -> str | None:
        """Identify a vendor robot account shared across declarations."""
        return None

    async def _call(
        self, channel: ChannelT, operation: str, **data: Any
    ) -> Any:
        """Admit one operation for the connection and the calling plugin."""
        key = (channel.owner_id, channel.connection_name)
        if self._connections.get(key) is not channel:
            raise self.send_error("Channel does not belong to this adapter")
        current = get_current_plugin()
        if current is not None and current is not channel.owner:
            raise PermissionError("Connection belongs to another plugin")
        if operation not in channel.supported_operations:
            raise UnsupportedOperation(
                f"Unsupported {self.get_name()} channel operation: {operation}"
            )
        if operation in MESSAGE_OPERATIONS:
            event = data.get("event")
            if not isinstance(event, self.message_event_type):
                raise self.send_error(
                    f"{self.get_name()} message operations require a "
                    f"{self.message_event_type.__name__}"
                )
            if event.connection_id != channel.self_id:
                raise self.send_error(
                    "Reply event belongs to another connection"
                )
            if not channel.replies_available:
                raise self.send_error(
                    f"Connection {channel.self_id} is not ready"
                )
        return await self._perform(channel, operation, **data)

    @abstractmethod
    async def _perform(
        self, channel: ChannelT, operation: str, **data: Any
    ) -> Any:
        """Execute one admitted protocol operation."""
        raise NotImplementedError
