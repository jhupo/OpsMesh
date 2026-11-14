"""QQ connection state and outgoing message rules."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import anyio

from opsmesh.adapters import Channel
from opsmesh.matchers import Matcher
from opsmesh.plugins.channels._dedupe import EventDeduplicator
from opsmesh.plugins.model import Plugin

from .config import QQConnectionSettings, QQSettings
from .errors import QQSendError
from .events import QQMessageEvent

if TYPE_CHECKING:
    from .adapter import QQAdapter

CONNECTING = "connecting"
READY = "ready"
FAILED = "failed"
DISCONNECTED = "disconnected"
CLOSED = "closed"

_SEQUENCE_LIMIT = 512
_MAX_REPLIES = 5


class QQChannel(Channel):
    """One declared QQ bot connection.

    The channel is connected to the driver only after its secret and
    callback route are usable.
    """

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types

    @property
    def supported_operations(self) -> frozenset[str]:
        """Return implemented operations, independent of account privileges."""
        from .groups import QQGroups

        return QQGroups.operations | {"send_message", "stream"}

    def __init__(
        self,
        adapter: QQAdapter,
        self_id: str,
        *,
        owner_id: str,
        owner: Plugin | None = None,
        connection_name: str,
        settings: QQConnectionSettings,
        protocol: QQSettings,
        path: str,
    ) -> None:
        """Initialize a declared but not yet active connection."""
        super().__init__(adapter, self_id)
        self._owner = owner
        self._owner_id = owner_id
        self._connection_name = connection_name
        self._settings = settings
        self._protocol = protocol
        self._path = path
        self._state = CONNECTING
        self._error: str | None = None
        self._dedupe = EventDeduplicator(
            ttl=protocol.dedupe_ttl, capacity=protocol.dedupe_capacity
        )
        self._sequences: OrderedDict[str, int] = OrderedDict()
        self._reply_locks: dict[str, anyio.Lock] = {}

    @property
    def owner_ready(self) -> bool:
        """Whether the declaring plugin is ready to accept work."""
        return self._owner is None or self._owner.ready

    def accepts_matcher(self, matcher: type[Matcher]) -> bool:
        """Limit dispatch to handlers owned by the declaring plugin."""
        return bool(
            matcher._source is not None
            and matcher._source.plugin_id == self.owner_id
        )

    @property
    def owner_id(self) -> str:
        """The plugin installation that declared this connection."""
        return self._owner_id

    @property
    def connection_name(self) -> str:
        """The declaring plugin's connection name."""
        return self._connection_name

    @property
    def settings(self) -> QQConnectionSettings:
        """The validated connection settings."""
        return self._settings

    @property
    def protocol(self) -> QQSettings:
        """The shared QQ protocol settings."""
        return self._protocol

    @property
    def path(self) -> str:
        """The callback path registered on the HTTP transport."""
        return self._path

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
        """Whether callbacks and sends for this connection are available."""
        return self._state == READY

    def mark_ready(self) -> None:
        """Mark the connection usable."""
        self._state = READY
        self._error = None

    def mark_failed(self, reason: str) -> None:
        """Mark the connection unusable with a safe reason."""
        self._state = FAILED
        self._error = reason

    def mark_disconnected(self) -> None:
        """Mark a previously ready connection unavailable."""
        self._state = DISCONNECTED
        self._error = "connection disconnected"

    def mark_closed(self) -> None:
        """Mark the connection permanently closed."""
        self._state = CLOSED
        self._error = "connection closed"

    def release_delivery(self, event_id: str) -> None:
        """Allow a vendor retry when admission failed before execution."""
        self._dedupe.forget(event_id)

    def first_delivery(self, event_id: str) -> bool:
        """Record an event identifier and report whether it is new."""
        return not self._dedupe.seen(event_id)

    def next_sequence(self, message_id: str) -> int:
        """Return the next reply sequence for one incoming message."""
        sequence = self._sequences.get(message_id, 0) + 1
        self._sequences[message_id] = sequence
        self._sequences.move_to_end(message_id)
        self._prune_sequences()
        return sequence

    @asynccontextmanager
    async def replies(self, message_id: str, count: int) -> AsyncIterator[None]:
        """Reserve reply capacity while serializing sends for one message.

        Only attempted sends consume sequence numbers. Upload failures and
        cancellation before sending leave the unused capacity available.
        """
        lock = self._reply_locks.setdefault(message_id, anyio.Lock())
        try:
            async with lock:
                if self._sequences.get(message_id, 0) + count > _MAX_REPLIES:
                    raise QQSendError(
                        "QQ allows at most five replies per message"
                    )
                yield
        finally:
            if not lock.locked() and not lock.statistics().tasks_waiting:
                self._reply_locks.pop(message_id, None)
            self._prune_sequences()

    def _prune_sequences(self) -> None:
        for message_id in tuple(self._sequences):
            if len(self._sequences) <= _SEQUENCE_LIMIT:
                break
            if message_id not in self._reply_locks:
                del self._sequences[message_id]

    async def send(
        self,
        event: QQMessageEvent,
        message: Any,
        **kwargs: Any,
    ) -> Any:
        """Reply to one incoming QQ message.

        Args:
            event: The incoming QQ message event.
            message: Text or framework message with attachment references.
            **kwargs: Reserved for future send options.

        Returns:
            The vendor response payload.
        """
        if not isinstance(event, QQMessageEvent):
            raise TypeError("QQChannel.send requires a QQMessageEvent")
        return await self.call(
            "send_message", event=event, message=message, **kwargs
        )
