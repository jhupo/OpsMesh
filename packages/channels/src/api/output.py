"""Materialize messages and observe actual HTTP writes separately."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import anyio

from ..receipts import Delivery
from ..sending import message_size, normalize, split_data
from ..streaming import TextBlock, message_stream
from .attachments import prepare_message
from .errors import HTTPError
from .responses import sse_event


@dataclass(frozen=True)
class OutputFrame:
    """Associate encoded bytes with the message actually written by ASGI."""

    body: bytes
    message_id: str
    offset: int = 0
    complete: bool = False


@dataclass
class _PendingReceipt:
    delivery: Delivery
    offset: int = 0
    complete: bool = False
    materialized: bool = False


class MessageOutput:
    """Keep dependency-bound materialization apart from transport completion."""

    def __init__(self, channel, event) -> None:
        """Own bounded message state for one authenticated request."""
        self.channel = channel
        self.event = event
        self.messages: list[dict[str, Any]] = []
        self.emit: Callable[[OutputFrame], Awaitable[None]] | None = None
        self.current_message_id: str | None = None
        self.failure: BaseException | None = None
        self._closed = False
        self._bytes = 0
        self._count = 0
        self._identifiers: set[str] = set()
        self._pending: dict[str, _PendingReceipt] = {}
        self._lock = anyio.Lock()

    def close(self) -> None:
        """Reject later business sends before dependencies are released."""
        self._closed = True

    def _admit(self, size: int = 0) -> None:
        if self._closed:
            raise RuntimeError("The message response has ended")
        if self.failure is not None:
            raise RuntimeError("The message response has failed")
        if self._bytes + size > self.channel.settings.max_output_bytes:
            raise HTTPError(500, "message output limit exceeded")
        self._bytes += size

    def _start_message(
        self, identifier: str, delivery: Delivery | None
    ) -> None:
        self._admit()
        if identifier in self._identifiers:
            raise ValueError("Duplicate response message ID")
        self._identifiers.add(identifier)
        self._count += 1
        if self._count > self.channel.settings.max_output_messages:
            raise HTTPError(500, "message output limit exceeded")
        if delivery is not None:
            delivery.check_owner()
            self._pending[identifier] = _PendingReceipt(delivery)

    async def send(
        self, value: Any, *, delivery: Delivery | None = None
    ) -> None:
        """Materialize a complete message and optional delivery observation."""
        async with self._lock:
            try:
                identifier = uuid4().hex
                self._start_message(identifier, delivery)
                await self._message(value, identifier)
            except BaseException as error:
                self.failure = error
                raise

    async def _message(self, value, identifier):
        message = normalize(value)
        content, data = split_data(message)
        size = message_size(message)
        self._admit(size)
        parts, uploads = await prepare_message(
            self.channel, self.event, content
        )
        item = {"message_id": identifier, "content": parts, "data": data}
        if uploads:
            self.channel.uploads.put_many(
                (self.event.installation_id, self.event.user_id), uploads
            )
        if identifier in self._pending:
            self._pending[identifier].materialized = True
        if self.emit is None:
            self.messages.append(item)
        else:
            await self.emit(
                OutputFrame(sse_event("message", item), identifier, size, True)
            )

    async def stream(self, source, *, delivery: Delivery | None = None) -> None:
        """Consume typed message blocks while their dependencies remain live."""
        async with self._lock:
            try:
                async with message_stream(source) as reader:
                    async for identifier, block in reader.blocks():
                        self._start_message(identifier, delivery)
                        self.current_message_id = identifier
                        if isinstance(block, TextBlock):
                            await self._text(block, identifier)
                        else:
                            await self._message(block, identifier)
                        self.current_message_id = None
            except BaseException as error:
                self.failure = error
                raise

    async def _text(self, block: TextBlock, identifier: str) -> None:
        if self.emit is None:
            await self._message(await block.message(), identifier)
            return
        self._admit(
            len(block.start.title.encode("utf-8"))
            if block.start.format == "markdown"
            else 0
        )
        await self.emit(
            OutputFrame(
                sse_event(
                    "message_start",
                    {
                        "message_id": identifier,
                        "format": block.start.format,
                        "title": block.start.title,
                    },
                ),
                identifier,
            )
        )
        offset = 0
        sequence = 0
        async for text in block.chunks():
            self._admit(len(text.encode("utf-8")))
            offset += len(text.encode("utf-8"))
            sequence += 1
            await self.emit(
                OutputFrame(
                    sse_event(
                        "message_delta",
                        {
                            "message_id": identifier,
                            "sequence": sequence,
                            "delta": text,
                        },
                    ),
                    identifier,
                    offset,
                )
            )
        await self.emit(
            OutputFrame(
                sse_event("message_end", {"message_id": identifier}),
                identifier,
                offset,
                True,
            )
        )

    async def written(self, frame: OutputFrame) -> None:
        """Called only after the native ASGI send returns successfully."""
        pending = self._pending.get(frame.message_id)
        if pending is None or pending.complete:
            return
        pending.offset = frame.offset
        if frame.complete:
            pending.complete = True
            await pending.delivery.record(
                frame.message_id, "written", offset=frame.offset
            )

    async def finish_transport(self, error=None) -> None:
        """Finalize pending operations; keep successful writes."""
        for identifier, pending in self._pending.items():
            if pending.complete:
                continue
            pending.complete = True
            failure = error or (self.failure if self.emit is not None else None)
            await pending.delivery.record(
                identifier,
                "unknown"
                if failure is not None
                else ("written" if pending.materialized else "failed"),
                offset=pending.offset,
                error=type(failure).__name__ if failure else None,
            )

    def envelope(self, status: str, error: dict | None = None) -> dict:
        """Return encoded messages without borrowing request resources."""
        return {
            "conversation_id": self.event.conversation_id,
            "status": status,
            "messages": self.messages,
            "error": error,
        }
