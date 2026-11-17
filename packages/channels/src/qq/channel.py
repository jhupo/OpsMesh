"""QQ connection state and native reply limits."""

from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio

from opsmesh.plugins.model import Plugin

from ..channel import MESSAGE_OPERATIONS, CallbackChannel
from .client import QQClient
from .config import QQConnectionSettings
from .errors import QQConfigurationError, QQSendError
from .gateway import QQStream
from .operations import QQOperations

_SEQUENCE_LIMIT = 512
_MAX_GROUP_REPLIES = 5


class QQChannel(CallbackChannel[QQConnectionSettings]):
    """One declared QQ bot connection.

    The connection joins the driver index only after its Gateway handshake
    succeeds.
    """

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types
    stream_formats = frozenset()
    supported_operations = QQOperations.operations | MESSAGE_OPERATIONS

    def __init__(
        self,
        adapter: Any,
        *,
        owner: Plugin,
        connection_name: str,
        settings: QQConnectionSettings,
    ) -> None:
        """Initialize declared reply state."""
        super().__init__(
            adapter,
            owner=owner,
            connection_name=connection_name,
            settings=settings,
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

    def _read_secret(self) -> str:
        reference = self.settings.secret_ref
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise QQConfigurationError(
                f"Environment variable {reference} is not set"
            )
        return secret.strip()

    def _open_resources(self) -> None:
        self._client = QQClient(self.settings, self._secret, name=self.self_id)

    async def _close_resources(self) -> None:
        await self.client.close()

    def _create_inbound_task(self) -> QQStream:
        return QQStream(self)
