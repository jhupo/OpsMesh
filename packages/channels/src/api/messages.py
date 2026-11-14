"""Validate API content and convert it to common message segments."""

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..attachments import Attachment, AttachmentCollection
from ..messages import Message, MessageSegment
from .errors import HTTPError


class TextContent(BaseModel):
    """Text within an ordered API message."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=8000)


class AttachmentContent(BaseModel):
    """Reference an already uploaded file; URLs and paths are not accepted."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["image", "file", "audio", "video"]
    attachment_id: str = Field(pattern=r"^[a-f0-9]{32}$")


MessageContent = Annotated[
    TextContent | AttachmentContent, Field(discriminator="type")
]
MessageInput = (
    Annotated[str, Field(min_length=1, max_length=8000)]
    | Annotated[list[MessageContent], Field(min_length=1, max_length=32)]
)


class MessageRequest(BaseModel):
    """The complete public API message request contract."""

    model_config = ConfigDict(extra="forbid")
    message: MessageInput
    conversation_id: str | None = None

    @field_validator("conversation_id")
    @classmethod
    def canonical_conversation(cls, value: str | None) -> str | None:
        """Accept only canonical UUID strings as conversation separators."""
        if value is not None and str(UUID(value)) != value:
            raise ValueError("conversation_id must be a canonical UUID")
        return value

    @field_validator("message")
    @classmethod
    def nonblank_text(cls, value: MessageInput) -> MessageInput:
        """Reject whitespace-only plain messages."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("Message text must not be blank")
        if isinstance(value, list) and not any(
            isinstance(part, AttachmentContent) or part.text.strip()
            for part in value
        ):
            raise ValueError("Message text must not be blank")
        return value


def from_request(
    request: MessageRequest,
    files: AttachmentCollection,
    resolve: Callable[[str, AttachmentCollection], Attachment] | None = None,
) -> Message:
    """Resolve each attachment under the authenticated route owner."""
    value = request.message
    if isinstance(value, str):
        return Message(value)
    message = Message()
    for part in value:
        if isinstance(part, TextContent):
            message.append(MessageSegment.text(part.text))
        else:
            if resolve is None:
                raise HTTPError(422, "Attachment transfer is unavailable")
            metadata = resolve(part.attachment_id, files)
            segment = MessageSegment.attachment(metadata)
            if segment.type != part.type and part.type != "file":
                raise HTTPError(422, "Attachment type does not match content")
            message.append(MessageSegment(part.type, segment.data))
    return message
