"""Shared transport messages built on the framework message protocol."""

from opsmesh.messages import Message as FrameworkMessage
from opsmesh.messages import MessageSegment as FrameworkSegment

from .attachments import Attachment

MEDIA_TYPES = frozenset({"image", "file", "audio", "video"})


class MessageSegment(FrameworkSegment["Message"]):
    """A text, mention, or opaque attachment reference."""

    @classmethod
    def get_message_class(cls) -> type["Message"]:
        """Return the shared transport message class."""
        return Message

    def is_text(self) -> bool:
        """Identify segments that participate in text rules."""
        return self.type == "text"

    def __str__(self) -> str:
        """Expose only text, never attachment metadata or credentials."""
        return str(self.data.get("text", "")) if self.is_text() else ""

    @classmethod
    def text(cls, text: str) -> "MessageSegment":
        """Construct one text segment."""
        return cls("text", {"text": text})

    @classmethod
    def attachment(
        cls,
        attachment: Attachment,
        *,
        duration_ms: int | None = None,
        thumbnail: Attachment | None = None,
    ) -> "MessageSegment":
        """Reference media, with playback duration and cover when required.

        Args:
            attachment: Metadata from the event's attachment collection.
            duration_ms: Actual playback duration; never an estimated default.
            thumbnail: Image in the same collection, used as a video cover.
        """
        category = attachment.media_type.split("/", 1)[0]
        kind = category if category in {"image", "audio", "video"} else "file"
        data: dict[str, str | int] = {"attachment_id": attachment.id}
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        if thumbnail is not None:
            data["thumbnail_id"] = thumbnail.id
        return cls(kind, data)

    @classmethod
    def mention(cls, user_id: str) -> "MessageSegment":
        """Represent a platform-identified mention."""
        return cls("mention", {"user_id": user_id})


class Message(FrameworkMessage[MessageSegment]):
    """Ordered segments shared by API and bot channels."""

    @classmethod
    def get_segment_class(cls) -> type[MessageSegment]:
        """Return the shared segment type."""
        return MessageSegment

    @staticmethod
    def _construct(msg: str) -> list[MessageSegment]:
        return [MessageSegment.text(msg)] if msg else []
