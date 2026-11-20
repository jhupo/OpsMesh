"""Validate channel content before platform conversion."""

import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)


class ContentData(BaseModel):
    """Reject coercions and undeclared segment fields at the boundary."""

    model_config = ConfigDict(strict=True, extra="forbid")


class TextData(ContentData):
    """Plain text payload without automatic format detection."""

    text: str


class MarkdownData(TextData):
    """Explicit Markdown payload and notification title."""

    title: str = Field(default="Reply", min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_title(self):
        """Require a readable notification title."""
        if not self.title.strip():
            raise ValueError("Markdown title cannot be blank")
        return self


class TableData(ContentData):
    """Bounded rectangular data for display or transport."""

    columns: list[str] = Field(min_length=1, max_length=32)
    rows: list[list[str | int | float | bool | None]] = Field(max_length=200)
    caption: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_shape(self):
        """Bound dimensions and reject nonfinite or oversized content."""
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Table rows must match the column count")
        encoded = json.dumps(self.model_dump(), allow_nan=False)
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("Table exceeds the size limit")
        return self


class MediaData(ContentData):
    """Opaque attachment reference with optional playback metadata."""

    attachment_id: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, gt=0)
    thumbnail_id: str | None = None
    alt: str | None = None


class MentionData(ContentData):
    """Platform-identified recipient reference."""

    user_id: str = Field(min_length=1)


class StructuredData(ContentData):
    """Caller-facing JSON data accompanying readable content."""

    value: dict


SCHEMAS = {
    "text": TextData,
    "markdown": MarkdownData,
    "table": TableData,
    "image": MediaData,
    "file": MediaData,
    "audio": MediaData,
    "video": MediaData,
    "mention": MentionData,
    "data": StructuredData,
}


def validate_segment(segment):
    """Return an independent validated copy of one message segment."""
    from .segments import MessageSegment

    schema = SCHEMAS.get(segment.type)
    if schema is None:
        raise ValueError(f"Unsupported outgoing segment: {segment.type}")
    try:
        data = schema.model_validate(segment.data).model_dump(exclude_none=True)
    except ValidationError as error:
        if schema is MediaData:
            from ..attachments import AttachmentError

            raise AttachmentError(str(error)) from error
        raise
    if segment.type == "data":
        return MessageSegment.structured(data["value"])
    return MessageSegment(segment.type, data)


TextFormat = Literal["text", "markdown"]
