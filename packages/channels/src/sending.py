"""Validate ordered outgoing content before a transport sends anything."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any

from opsmesh.messages import Message as FrameworkMessage
from opsmesh.messages import MessageSegment as FrameworkSegment

from .attachments import Attachment, AttachmentCollection, AttachmentError
from .messages import MEDIA_TYPES, Message, MessageSegment
from .operations import UnsupportedOperation

MAX_STREAM_BYTES = 1024 * 1024
_STREAM_CLEANUP_TIMEOUT = 10


@dataclass(frozen=True)
class OutgoingMarkdown:
    """Keep explicit formatting intact until the transport encodes it."""

    text: str
    title: str


@dataclass(frozen=True)
class OutgoingAttachment:
    """An admitted attachment and optional playback information."""

    attachment: Attachment
    content: bytes
    kind: str
    duration_ms: int | None = None
    thumbnail: Attachment | None = None
    thumbnail_content: bytes | None = None


@dataclass(frozen=True)
class PreparedMessage:
    """Resolved send parts and their original framework segment types."""

    parts: list[str | OutgoingMarkdown | OutgoingAttachment]
    kinds: list[str]


class MessageSendError(RuntimeError):
    """A split send stopped; completed parts must not be sent again."""

    def __init__(
        self, completed: list[dict[str, Any]], failed_index: int
    ) -> None:
        """Retain confirmed receipts and the zero-based failed part index."""
        self.completed = tuple(completed)
        self.failed_index = failed_index
        super().__init__(
            f"Message part {failed_index + 1} failed after "
            f"{len(completed)} confirmed sends; delivery of the failed part "
            "may be unknown. The message was not retried."
        )


def image_metadata(attachment: Attachment, content: bytes) -> Attachment:
    """Correct vendor MIME guesses from supported image signatures."""
    for signature, mime in (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
    ):
        if content.startswith(signature):
            return replace(attachment, media_type=mime)
    return attachment


def normalize(message: str | FrameworkMessage | FrameworkSegment) -> Message:
    """Validate a copy so direct segment construction cannot bypass schemas."""
    from .messages.schemas import validate_segment

    if isinstance(message, str):
        message = Message(message)
    elif isinstance(message, FrameworkSegment):
        message = Message(message)
    if not isinstance(message, FrameworkMessage):
        raise TypeError("Expected a string or framework message")
    return Message(validate_segment(part) for part in message)


def message_size(message: Message) -> int:
    """Count UTF-8 content, excluding transport envelopes and file bytes."""
    import json

    size = 0
    for part in message:
        if part.is_text():
            size += len(str(part).encode("utf-8"))
            if part.type == "markdown":
                size += len(part.data["title"].encode("utf-8"))
        else:
            size += len(
                json.dumps(
                    part.data, ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            )
    return size


def require_supported(message: Message, supported: frozenset[str]) -> None:
    """Reject unsupported content before resolving files or sending."""
    unsupported = {part.type for part in message} - supported
    if unsupported:
        raise UnsupportedOperation(
            "Unsupported message types: " + ", ".join(sorted(unsupported))
        )


def split_data(message: Message) -> tuple[Message, dict[str, Any] | None]:
    """Validate outbound structured data and retain readable segments."""
    content = Message()
    data = None
    for segment in message:
        if segment.type != "data":
            content.append(segment)
            continue
        if data is not None:
            raise ValueError("A message accepts at most one data segment")
        value = segment.data.get("value")
        if not isinstance(value, dict):
            raise TypeError("Structured message data must be a JSON object")
        data = value
    if not any(
        part.type in MEDIA_TYPES | {"table"}
        or (part.is_text() and str(part).strip())
        for part in content
    ):
        raise ValueError("Messages cannot be empty; provide readable content")
    if len(message) > 32:
        raise ValueError("A reply accepts at most 32 segments")
    return content, data


async def close_source(source: AsyncIterator[object]) -> None:
    """Release a caller-owned iterator before request dependencies close."""
    import anyio

    close = getattr(source, "aclose", None)
    if close is not None:
        with anyio.fail_after(_STREAM_CLEANUP_TIMEOUT, shield=True):
            await close()


async def prepare_attachment(
    segment: MessageSegment, files: AttachmentCollection
) -> OutgoingAttachment:
    """Validate and materialize one media reference for any transport."""
    key = segment.data.get("attachment_id")
    if not isinstance(key, str):
        raise AttachmentError("Missing attachment reference")
    attachment = files.get(key)
    category = attachment.media_type.split("/", 1)[0]
    if segment.type != "file" and segment.type != category:
        raise AttachmentError("Attachment type does not match content")
    duration = segment.data.get("duration_ms")
    if duration is not None and (type(duration) is not int or duration <= 0):
        raise AttachmentError("Media duration must be positive milliseconds")
    thumbnail_id = segment.data.get("thumbnail_id")
    thumbnail = files.get(thumbnail_id) if thumbnail_id else None
    content = await files.read(key)
    if not content:
        raise AttachmentError("Cannot send an empty attachment")
    if segment.type == "image":
        attachment = image_metadata(attachment, content)
    thumbnail_content = await files.read(thumbnail.id) if thumbnail else None
    if thumbnail is not None:
        thumbnail = image_metadata(thumbnail, thumbnail_content)
    return OutgoingAttachment(
        attachment,
        content,
        segment.type,
        duration,
        thumbnail,
        thumbnail_content,
    )


async def prepare(
    message: str | FrameworkMessage | FrameworkSegment,
    files: AttachmentCollection,
    text_limit: int,
    *,
    supported: frozenset[str],
) -> PreparedMessage:
    """Resolve every file before visible sends; preserve text/media order."""
    message = normalize(message)
    require_supported(message, supported)
    kinds = [
        part.type
        for part in message
        if part.type != "data" and (not part.is_text() or str(part).strip())
    ]
    segments, _ = split_data(message)
    parts: list[str | OutgoingMarkdown | OutgoingAttachment] = []
    attachment_ids: set[str] = set()
    for segment in segments:
        if segment.type == "markdown":
            parts.append(OutgoingMarkdown(str(segment), segment.data["title"]))
        elif segment.is_text():
            text = str(segment)
            parts.append(text)
        elif segment.type in MEDIA_TYPES:
            part = await prepare_attachment(segment, files)
            attachment_ids.add(part.attachment.id)
            if part.thumbnail is not None:
                attachment_ids.add(part.thumbnail.id)
            parts.append(part)
        else:
            raise ValueError(f"Unsupported outgoing segment: {segment.type}")
    files.validate_message(attachment_ids)
    parts = [
        part
        for part in parts
        if (
            part.text.strip()
            if isinstance(part, OutgoingMarkdown)
            else not isinstance(part, str) or part.strip()
        )
    ]
    if not parts:
        raise ValueError("Messages cannot be empty")
    if any(
        len(part.text if isinstance(part, OutgoingMarkdown) else part)
        > text_limit
        for part in parts
        if isinstance(part, (str, OutgoingMarkdown))
    ):
        raise ValueError(f"Text parts are limited to {text_limit} characters")
    return PreparedMessage(parts, kinds)
