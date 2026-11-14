"""Bounded complete messages and incremental text for one API request."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

import anyio

from ..sending import close_source, normalize, split_data
from .attachments import encode_message
from .errors import HTTPError
from .responses import encode_json, sse_event

_MAX_DELTA_BYTES = 64 * 1024


class MessageOutput:
    """Materialize output while its dependencies and attachments are live."""

    def __init__(self, channel, event) -> None:
        """Own counters and the transport sink for one authenticated event."""
        self.channel = channel
        self.event = event
        self.messages: list[dict[str, Any]] = []
        self.emit: Callable[[bytes], Awaitable[None]] | None = None
        self.current_message_id: str | None = None
        self.failure: BaseException | None = None
        self._closed = False
        self._bytes = 0
        self._count = 0
        self._lock = anyio.Lock()

    def close(self) -> None:
        """Reject business sends before dependency finalizers start."""
        self._closed = True

    def _admit(self, size: int = 0) -> None:
        if self._closed:
            raise RuntimeError("The message response has ended")
        if self.failure is not None:
            raise RuntimeError("The message response has failed")
        if self._bytes + size > self.channel.settings.max_output_bytes:
            raise HTTPError(500, "message output limit exceeded")
        self._bytes += size

    def _start_message(self) -> str:
        self._admit()
        if self._count >= self.channel.settings.max_output_messages:
            raise HTTPError(500, "message output limit exceeded")
        self._count += 1
        return uuid4().hex

    async def send(self, value: Any) -> None:
        """Emit one complete message without merging adjacent sends."""
        async with self._lock:
            try:
                identifier = self._start_message()
                content, data = split_data(normalize(value))
                item = {
                    "content": await encode_message(
                        self.channel, self.event, content
                    ),
                    "data": data,
                }
                self._admit(len(encode_json(item)))
                if self.emit is None:
                    self.messages.append(item)
                else:
                    await self.emit(
                        sse_event("message", {"message_id": identifier, **item})
                    )
            except BaseException as error:
                self.failure = error
                raise

    async def stream(self, source: AsyncIterator[str]) -> None:
        """Consume text increments before returning to the caller."""
        async with self._lock:
            failure = None
            try:
                identifier = self._start_message()
                self.current_message_id = identifier
                if self.emit is not None:
                    await self.emit(
                        sse_event("message_start", {"message_id": identifier})
                    )
                chunks: list[str] = []
                sequence = 0
                has_text = False
                async for chunk in source:
                    if not isinstance(chunk, str):
                        raise TypeError("Stream increments must be text")
                    size = len(chunk.encode("utf-8"))
                    if size > _MAX_DELTA_BYTES:
                        raise HTTPError(500, "message increment limit exceeded")
                    self._admit(size)
                    if not chunk:
                        continue
                    has_text = has_text or bool(chunk.strip())
                    sequence += 1
                    if self.emit is None:
                        chunks.append(chunk)
                    else:
                        await self.emit(
                            sse_event(
                                "message_delta",
                                {
                                    "message_id": identifier,
                                    "sequence": sequence,
                                    "delta": chunk,
                                },
                            )
                        )
                if not has_text:
                    raise ValueError("Stream answers require readable content")
            except BaseException as error:
                failure = error
            finally:
                try:
                    await close_source(source)
                except BaseException as cleanup_error:
                    if failure is None:
                        failure = cleanup_error
                    else:
                        failure.add_note(
                            f"Stream cleanup failed: {cleanup_error!r}"
                        )
            if failure is not None:
                self.failure = failure
                raise failure
            if self.emit is None:
                self.messages.append(
                    {
                        "content": [{"type": "text", "text": "".join(chunks)}],
                        "data": None,
                    }
                )
            else:
                try:
                    await self.emit(
                        sse_event("message_end", {"message_id": identifier})
                    )
                except BaseException as error:
                    self.failure = error
                    raise
            self.current_message_id = None

    def envelope(self, status: str, error: dict | None = None) -> dict:
        """Return only already materialized response values."""
        return {
            "conversation_id": self.event.conversation_id,
            "status": status,
            "messages": self.messages,
            "error": error,
        }
