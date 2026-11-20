"""Ordered transport events, independent of model SDK events."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class StreamMessageStart:
    """Open one text block with an immutable identity and format."""

    message_id: str
    format: Literal["text", "markdown"] = "text"
    title: str = "Reply"


@dataclass(frozen=True)
class StreamTextDelta:
    """Append text at the next one-based sequence position."""

    message_id: str
    sequence: int
    text: str


@dataclass(frozen=True)
class StreamMessageEnd:
    """Complete the currently open text block."""

    message_id: str
