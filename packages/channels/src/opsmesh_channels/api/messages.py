"""Represent API input using the framework message protocol."""

from __future__ import annotations

from typing import Any

from opsmesh.framework.messages import Message, MessageSegment


class APIMessageSegment(MessageSegment["APIMessage"]):
    """A text segment or a structured JSON data segment."""

    @classmethod
    def get_message_class(cls) -> type[APIMessage]:
        """Return the API message sequence type."""
        return APIMessage

    def __str__(self) -> str:
        """Expose only text to command and text matching rules."""
        return self.data["text"] if self.is_text() else ""

    def is_text(self) -> bool:
        """Whether this segment carries plain text."""
        return self.type == "text"


class APIMessage(Message[APIMessageSegment]):
    """A framework text message or a structured operation's data."""

    @classmethod
    def get_segment_class(cls) -> type[APIMessageSegment]:
        """Return the API segment type."""
        return APIMessageSegment

    @staticmethod
    def _construct(msg: str) -> list[APIMessageSegment]:
        return [APIMessageSegment("text", {"text": msg})] if msg else []

    @classmethod
    def from_payload(cls, payload: Any) -> APIMessage:
        """Convert a JSON string or object into framework message segments.

        Strings and an object's string ``message`` field become plain text.
        Other JSON values remain data, so metadata cannot become a command.
        Text messages contain no data segments, so metadata cannot appear as
        command arguments. HTTPEvent retains the complete validated payload.
        """
        text = payload.get("message") if isinstance(payload, dict) else payload
        if isinstance(text, str):
            return cls(text)
        return cls(APIMessageSegment("data", {"value": payload}))
