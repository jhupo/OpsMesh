"""QQ connection state and outgoing message rules."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import anyio

from opsmesh.plugins.model import Plugin

from ..channel import RobotChannel
from .config import QQConnectionSettings, QQSettings
from .errors import QQSendError
from .events import QQMessageEvent

if TYPE_CHECKING:
    from .adapter import QQAdapter


_SEQUENCE_LIMIT = 512
_MAX_GROUP_REPLIES = 5


class QQChannel(RobotChannel[QQConnectionSettings, QQSettings]):
    """One declared QQ bot connection.

    The channel is connected to the driver only after its Gateway handshake
    succeeds.
    """

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types
    stream_formats = frozenset()

    @property
    def supported_operations(self) -> frozenset[str]:
        """Return implemented operations, independent of account privileges."""
        from .operations import QQOperations

        return QQOperations.operations | {"send_message", "stream"}

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
    ) -> None:
        """Initialize a declared but not yet active connection."""
        super().__init__(
            adapter,
            self_id,
            owner_id=owner_id,
            owner=owner,
            connection_name=connection_name,
            settings=settings,
            protocol=protocol,
        )
        self._sequences: OrderedDict[str, int] = OrderedDict()
        self._reply_locks: dict[str, anyio.Lock] = {}

    def next_sequence(self, message_id: str) -> int:
        """Return the next reply sequence for one incoming message."""
        sequence = self._sequences.get(message_id, 0) + 1
        self._sequences[message_id] = sequence
        self._sequences.move_to_end(message_id)
        self._prune_sequences()
        return sequence

    @asynccontextmanager
    async def replies(
        self,
        message_id: str,
        count: int,
        *,
        limit: int = _MAX_GROUP_REPLIES,
    ) -> AsyncIterator[None]:
        """Reserve reply capacity while serializing sends for one message.

        Only attempted sends consume sequence numbers. Upload failures and
        cancellation before sending leave the unused capacity available.
        """
        lock = self._reply_locks.setdefault(message_id, anyio.Lock())
        try:
            async with lock:
                if self._sequences.get(message_id, 0) + count > limit:
                    raise QQSendError(
                        f"QQ allows at most {limit} replies per message"
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
            **kwargs: Optional delivery observer.

        Returns:
            The vendor response payload.
        """
        if not isinstance(event, QQMessageEvent):
            raise TypeError("QQChannel.send requires a QQMessageEvent")
        return await self.call(
            "send_message", event=event, message=message, **kwargs
        )
