"""Validate ordered outgoing content before a transport sends anything."""

from dataclasses import dataclass, replace
from typing import Any

from opsmesh.messages import Message as FrameworkMessage
from opsmesh.messages import MessageSegment as FrameworkSegment

from .attachments import Attachment, AttachmentCollection, AttachmentError
from .messages import MEDIA_TYPES, Message, MessageSegment


@dataclass(frozen=True)
class OutgoingAttachment:
    """An admitted attachment and optional playback information."""

    attachment: Attachment
    content: bytes
    kind: str
    duration_ms: int | None = None
    thumbnail: Attachment | None = None
    thumbnail_content: bytes | None = None


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
    """Copy supported framework segments without changing their order."""
    if isinstance(message, str):
        return Message(message)
    if isinstance(message, FrameworkSegment):
        return Message(MessageSegment(message.type, dict(message.data)))
    if isinstance(message, FrameworkMessage):
        return Message(
            MessageSegment(part.type, dict(part.data)) for part in message
        )
    raise TypeError("Expected a string or framework message")


async def prepare(
    message: str | FrameworkMessage | FrameworkSegment,
    files: AttachmentCollection,
    text_limit: int,
) -> list[str | OutgoingAttachment]:
    """Resolve every file before visible sends; preserve text/media order."""
    segments = normalize(message)
    if len(segments) > 32:
        raise ValueError("A reply accepts at most 32 segments")
    parts: list[str | OutgoingAttachment] = []
    attachment_ids: set[str] = set()
    for segment in segments:
        if segment.is_text():
            text = str(segment)
            if parts and isinstance(parts[-1], str):
                parts[-1] += text
            else:
                parts.append(text)
        elif segment.type in MEDIA_TYPES:
            key = segment.data.get("attachment_id")
            if not isinstance(key, str):
                raise AttachmentError("Missing attachment reference")
            attachment = files.get(key)
            category = attachment.media_type.split("/", 1)[0]
            if segment.type != "file" and segment.type != category:
                raise AttachmentError("Attachment type does not match content")
            duration = segment.data.get("duration_ms")
            if duration is not None and (
                type(duration) is not int or duration <= 0
            ):
                raise AttachmentError(
                    "Media duration must be positive milliseconds"
                )
            thumbnail_id = segment.data.get("thumbnail_id")
            thumbnail = files.get(thumbnail_id) if thumbnail_id else None
            content = await files.read(key)
            attachment_ids.add(key)
            if not content:
                raise AttachmentError("Cannot send an empty attachment")
            if segment.type == "image":
                attachment = image_metadata(attachment, content)
            thumbnail_content = (
                await files.read(thumbnail.id) if thumbnail else None
            )
            if thumbnail is not None:
                attachment_ids.add(thumbnail.id)
                thumbnail = image_metadata(thumbnail, thumbnail_content)
            parts.append(
                OutgoingAttachment(
                    attachment,
                    content,
                    segment.type,
                    duration,
                    thumbnail,
                    thumbnail_content,
                )
            )
        else:
            raise ValueError(f"Unsupported outgoing segment: {segment.type}")
    files.validate_message(attachment_ids)
    parts = [
        part for part in parts if not isinstance(part, str) or part.strip()
    ]
    if not parts:
        raise ValueError("Messages cannot be empty")
    if any(isinstance(part, str) and len(part) > text_limit for part in parts):
        raise ValueError(f"Text parts are limited to {text_limit} characters")
    return parts
