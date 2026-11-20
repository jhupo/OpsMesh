"""Consume one ordered message stream without SDK or background tasks."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import anyio

from .messages import (
    Message,
    MessageSegment,
    StreamMessageEnd,
    StreamMessageStart,
    StreamTextDelta,
)
from .operations import UnsupportedOperation
from .sending import MAX_STREAM_BYTES, close_source, message_size

StreamItem = Message | StreamMessageStart | StreamTextDelta | StreamMessageEnd
MAX_STREAM_MESSAGES = 100
MAX_STREAM_EVENTS = 100000
MAX_DELTA_BYTES = 64 * 1024


class TextBlock:
    """Borrow exactly one open text block from a sequential stream reader."""

    def __init__(self, reader: "MessageStream", start: StreamMessageStart):
        """Initialize local stream state without starting background work."""
        self.reader = reader
        self.start = start
        self.complete = False

    async def chunks(self) -> AsyncIterator[str]:
        """Require contiguous deltas followed by the matching end marker."""
        sequence = 1
        readable = False
        while True:
            item = await self.reader.next_item()
            if isinstance(item, StreamMessageEnd):
                if item.message_id != self.start.message_id or not readable:
                    raise ValueError("Invalid or empty stream message end")
                self.complete = True
                return
            if not isinstance(item, StreamTextDelta):
                raise ValueError(
                    "A text block requires deltas followed by an end"
                )
            if (
                item.message_id != self.start.message_id
                or item.sequence != sequence
            ):
                raise ValueError("Out-of-order text delta")
            if type(item.sequence) is not int or not isinstance(item.text, str):
                raise TypeError("Invalid text delta")
            size = len(item.text.encode("utf-8"))
            if size > MAX_DELTA_BYTES:
                raise ValueError("Text delta exceeds the size limit")
            self.reader.account(size)
            sequence += 1
            readable = readable or bool(item.text.strip())
            yield item.text

    async def message(self) -> Message:
        """Buffer this bounded block for transports without live rendering."""
        text = "".join([chunk async for chunk in self.chunks()])
        segment = (
            MessageSegment.markdown(text, title=self.start.title)
            if self.start.format == "markdown"
            else MessageSegment.text(text)
        )
        return Message(segment)


class MessageStream:
    """Enforce sequence and budgets once for all outgoing transports."""

    def __init__(self, source: AsyncIterator[StreamItem]):
        """Initialize local stream state without starting background work."""
        self.source = source.__aiter__()
        self.identifiers: set[str] = set()
        self.count = 0
        self.size = 0
        self.events = 0

    def account(self, size: int) -> None:
        """Bound cumulative serialized output size."""
        self.size += size
        if self.size > MAX_STREAM_BYTES:
            raise ValueError("Stream output exceeds the size limit")

    async def next_item(self):
        """Read an event within the active text block deadline."""
        self.events += 1
        if self.events > MAX_STREAM_EVENTS:
            raise ValueError("Too many stream events")
        try:
            with anyio.fail_after(60):
                return await anext(self.source)
        except StopAsyncIteration:
            raise ValueError("Stream ended inside a text block") from None

    def identify(self, identifier: str) -> None:
        """Reserve a unique response message identifier."""
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier) > 128
        ):
            raise ValueError("Invalid stream message ID")
        if identifier in self.identifiers:
            raise ValueError("Duplicate stream message ID")
        self.identifiers.add(identifier)
        self.count += 1
        if self.count > MAX_STREAM_MESSAGES:
            raise ValueError("Too many stream messages")

    async def blocks(self):
        """Yield complete messages or one exclusively borrowed text block."""
        while True:
            with anyio.fail_after(60):
                try:
                    item = await anext(self.source)
                except StopAsyncIteration:
                    break
            self.events += 1
            if self.events > MAX_STREAM_EVENTS:
                raise ValueError("Too many stream events")
            if isinstance(item, Message):
                identifier = uuid4().hex
                self.identify(identifier)
                message = item
                self.account(message_size(message))
                yield identifier, message
            elif isinstance(item, StreamMessageStart):
                self.identify(item.message_id)
                if item.format not in {"text", "markdown"}:
                    raise ValueError("Unknown stream text format")
                if (
                    not isinstance(item.title, str)
                    or not item.title.strip()
                    or len(item.title) > 100
                ):
                    raise ValueError("Invalid stream title")
                if item.format == "markdown":
                    self.account(len(item.title.encode("utf-8")))
                block = TextBlock(self, item)
                yield item.message_id, block
                if not block.complete:
                    raise RuntimeError(
                        "The previous text block was not consumed"
                    )
            else:
                raise TypeError(
                    "Expected a complete Message or StreamMessageStart"
                )
        if not self.count:
            raise ValueError("Stream output cannot be empty")


@asynccontextmanager
async def message_stream(source):
    """Close the caller's source on completion, cancellation or rejection."""
    failure = None
    try:
        yield MessageStream(source)
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            await close_source(source)
        except BaseException as error:
            if failure is None:
                raise
            failure.add_note(f"Stream cleanup failed: {type(error).__name__}")


async def send_stream(source, send, *, card=None):
    """Send sequential blocks using the connection's normal message sender."""
    replies = []
    async with message_stream(source) as reader:
        async for identifier, block in reader.blocks():
            if isinstance(block, TextBlock):
                if card is not None and block.start.format == "markdown":
                    replies.append(await card(block, identifier))
                    continue
                raise UnsupportedOperation(
                    "This connection does not support "
                    "this incremental text format"
                )
            replies.append(await send(block, identifier))
    return replies
