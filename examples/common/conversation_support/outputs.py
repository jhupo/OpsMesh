"""Output decisions shared only by the conversation examples."""

import asyncio
import base64
from collections.abc import Callable
from contextlib import suppress
from uuid import uuid4

import anyio
from agents import AgentUpdatedStreamEvent, RunResultStreaming
from agents.items import ToolCallItem
from openai.types.responses import (
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
)
from openai.types.responses.response_output_item import ImageGenerationCall

from conversation_support.files import Deliverables
from opsmesh.plugins.channels import (
    Attachment,
    Message,
    MessageSegment,
    StreamMessageEnd,
    StreamMessageStart,
    StreamTextDelta,
)
from opsmesh.plugins.channels.sending import require_supported

OUTPUT_LIMIT = 1024 * 1024
SEND_TIMEOUT = 60


def render_table(data: dict) -> str:
    """Render a table for channels that do not support table segments."""
    lines = [data["caption"]] if data.get("caption") else []
    for index, row in enumerate(data["rows"], 1):
        lines.append(f"{index}.")
        for column, value in zip(data["columns"], row, strict=True):
            lines.append(f"{column}: {value if value is not None else 'null'}")
    if not data["rows"]:
        lines.append(" | ".join(data["columns"]))
    return "\n".join(lines)


class ConversationOutput:
    """Select public content while preserving the native execution result."""

    def __init__(
        self,
        *,
        format="markdown",
        publication="buffered",
        files: Deliverables | None = None,
        channel=None,
        structured: Callable[[object], Message] | None = None,
    ):
        """Bind only this invocation's output policy and optional files."""
        if format not in {"text", "markdown"}:
            raise ValueError("Unknown output format")
        if publication not in {"buffered", "progressive"}:
            raise ValueError("Unknown publication policy")
        if structured is not None and publication != "buffered":
            raise ValueError("Structured output requires buffered publication")
        self.send_types = (
            channel.send_types
            if channel is not None
            else frozenset(
                {
                    "text",
                    "markdown",
                    "table",
                    "data",
                    "image",
                    "file",
                    "audio",
                    "video",
                }
            )
        )
        self.format = format if format in self.send_types else "text"
        if channel is not None and self.format not in channel.stream_formats:
            publication = "buffered"
        self.publication = publication
        self.files = files
        self.structured = structured
        self._used = False

    def _present(self, message: Message) -> Message:
        """Choose lossless text fallbacks and omit HTTP-only metadata."""
        content = Message()
        for part in message:
            if part.type == "data" and "data" not in self.send_types:
                continue
            if part.type == "table" and "table" not in self.send_types:
                part = MessageSegment.text(render_table(part.data))
            elif part.type == "markdown" and "markdown" not in self.send_types:
                part = MessageSegment.text(str(part))
            content.append(part)
        require_supported(content, self.send_types)
        return content

    def _text(self, text: str) -> Message:
        if len(text.encode("utf-8")) > OUTPUT_LIMIT:
            raise ValueError("Model output exceeds the size limit")
        return Message(
            MessageSegment.markdown(text)
            if self.format == "markdown"
            else MessageSegment.text(text)
        )

    def _images(self, result) -> None:
        if self.files is None:
            return
        seen = set()
        for item in result.new_items:
            if not isinstance(item, ToolCallItem):
                continue
            raw = item.raw_item
            if not isinstance(raw, ImageGenerationCall) or raw.id in seen:
                continue
            if raw.status != "completed" or not raw.result:
                continue
            seen.add(raw.id)
            limit = self.files.attachments.settings.max_file_bytes
            if len(raw.result) > 4 * ((limit + 2) // 3):
                raise ValueError("Generated image exceeds the size limit")
            content = base64.b64decode(raw.result, validate=True)
            signatures = (
                (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
                (b"\xff\xd8\xff", "jpg", "image/jpeg"),
            )
            kind = next(
                (item for item in signatures if content.startswith(item[0])),
                None,
            )
            if kind is None:
                raise ValueError("Unsupported generated image format")
            attachment = self.files.add(
                Attachment.create(
                    f"image-{len(seen)}.{kind[1]}", kind[2], len(content)
                ),
                content,
            )
            self.files.select(attachment.id)

    async def stream(self, result: RunResultStreaming):
        """Own one SDK consumer and cancel it when delivery cannot continue."""
        if self._used:
            raise RuntimeError("This output has already consumed a result")
        self._used = True
        queue = asyncio.Queue(maxsize=32)
        ended = object()
        failure = None

        async def emit(item):
            await asyncio.wait_for(queue.put(item), SEND_TIMEOUT)

        async def consume():
            nonlocal failure
            try:
                await self._consume(result, emit)
            except BaseException as error:
                failure = error
                result.cancel()
            finally:
                # Drain queued output before checking task completion.
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(ended)

        task = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(300):
                while True:
                    if task.done() and queue.empty():
                        break
                    item = await queue.get()
                    if item is ended:
                        break
                    if isinstance(item, Message):
                        item = self._present(item)
                    yield item
                if failure is not None:
                    raise failure
        finally:
            if not task.done():
                result.cancel()
                task.cancel()
            # execution.stream closes the SDK; stop only our consumer here.
            with (
                anyio.CancelScope(shield=True),
                suppress(asyncio.CancelledError),
            ):
                await task

    async def _consume(self, result, emit):
        size = 0
        active = None
        sequence = 0
        collected = {}
        emitted = False
        response = 0
        async for event in result.stream_events():
            if isinstance(event, AgentUpdatedStreamEvent):
                if (
                    self.publication == "progressive"
                    and event.new_agent.output_type not in (None, str)
                ):
                    raise ValueError(
                        "Structured agents require buffered publication"
                    )
            if event.type != "raw_response_event":
                continue
            data = event.data
            if data.type == "response.created":
                response += 1
            if not isinstance(
                data, (ResponseTextDeltaEvent, ResponseTextDoneEvent)
            ):
                continue
            key = (
                response,
                data.item_id,
                data.output_index,
                data.content_index,
            )
            previous = collected.get(key, "")
            if isinstance(data, ResponseTextDeltaEvent):
                text = data.delta
            else:
                if not data.text.startswith(previous):
                    raise ValueError(
                        "Completed model text differs from its deltas"
                    )
                text = data.text[len(previous) :]
            collected[key] = previous + text
            size += len(text.encode("utf-8"))
            if size > OUTPUT_LIMIT:
                raise ValueError("Model output exceeds the size limit")
            if self.publication != "progressive" or not text:
                continue
            if active is None or active[0] != key:
                if active is not None:
                    await emit(StreamMessageEnd(active[1]))
                active = (key, uuid4().hex)
                sequence = 0
                await emit(StreamMessageStart(active[1], self.format))
            sequence += 1
            await emit(StreamTextDelta(active[1], sequence, text))
            emitted = True
        if result.interruptions:
            raise RuntimeError(
                "Approval continuation is not supported by this example"
            )
        if active is not None:
            await emit(StreamMessageEnd(active[1]))
        if self.structured is not None:
            message = self.structured(result.final_output)
            if not isinstance(message, Message):
                raise TypeError(
                    "Structured output must produce a channel Message"
                )
            await emit(message)
        elif not isinstance(result.final_output, str):
            raise TypeError("Provide a formatter for structured model output")
        elif not emitted and result.final_output.strip():
            await emit(self._text(result.final_output))
        self._images(result)
        if self.files is not None and self.files.entries:
            await emit(self.files.message())
