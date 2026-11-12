"""QQ official bot message segments and callback events."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from opsmesh.adapters import Event
from opsmesh.messages import Message, MessageSegment

from .errors import QQProtocolError

C2C_MESSAGE = "C2C_MESSAGE_CREATE"
GROUP_MESSAGE = "GROUP_AT_MESSAGE_CREATE"
MESSAGE_EVENTS = frozenset({C2C_MESSAGE, GROUP_MESSAGE})


class QQMessageSegment(MessageSegment["QQMessage"]):
    """One QQ text segment."""

    @classmethod
    def get_message_class(cls) -> type[QQMessage]:
        """Return the QQ message sequence type."""
        return QQMessage

    def __str__(self) -> str:
        """Return the segment text used for command matching."""
        return str(self.data.get("text", ""))

    def is_text(self) -> bool:
        """Whether this segment carries plain text."""
        return self.type == "text"


class QQMessage(Message[QQMessageSegment]):
    """A sequence of QQ text segments."""

    @classmethod
    def get_segment_class(cls) -> type[QQMessageSegment]:
        """Return the QQ segment type."""
        return QQMessageSegment

    @staticmethod
    def _construct(msg: str) -> list[QQMessageSegment]:
        if not msg:
            return []
        return [QQMessageSegment("text", {"text": msg})]


class QQMessageEvent(Event):
    """A QQ direct or group message callback."""

    model_config = ConfigDict(extra="allow")

    connection_id: str
    user_id: str
    user_name: str | None = None
    group_id: str | None = None
    message_id: str
    event_id: str
    timestamp: int | None = None
    content: str
    raw: dict[str, Any]

    def get_type(self) -> str:
        """Return the framework message event type."""
        return "message"

    def get_event_name(self) -> str:
        """Return the QQ message event name."""
        return "qq.message"

    def get_event_description(self) -> str:
        """Describe the event without logging its message content."""
        return f"message {self.message_id} from {self.get_user_id()}"

    def get_user_id(self) -> str:
        """Return the caller identifier scoped by connection."""
        return f"{self.connection_id}:{self.user_id}"

    def get_session_id(self) -> str:
        """Return the conversation identifier scoped by connection."""
        if self.group_id is not None:
            return f"{self.connection_id}:group:{self.group_id}"
        return f"{self.connection_id}:private:{self.user_id}"

    def get_message(self) -> QQMessage:
        """Return the message content as QQ segments."""
        return QQMessage(self.content)

    def is_tome(self) -> bool:
        """QQ message events are always delivered to this bot."""
        return True

    @property
    def group_ids(self) -> tuple[str, ...]:
        """Return verified group identifiers for the caller mapping."""
        if self.group_id is None:
            return ()
        return (f"{self.connection_id}:group:{self.group_id}",)


def parse_callback(
    payload: dict[str, Any], *, connection_id: str
) -> QQMessageEvent | None:
    """Convert one dispatched QQ callback into a framework event.

    Args:
        payload: The verified callback body.
        connection_id: The owning connection identifier.

    Returns:
        A message event, or ``None`` for event types this plugin ignores.

    Raises:
        QQProtocolError: The body is a message event without required fields.
    """
    event_type = payload.get("t")
    if event_type not in MESSAGE_EVENTS:
        return None
    data = payload.get("d")
    if not isinstance(data, dict):
        raise QQProtocolError("QQ message event has no data object")
    content = data.get("content")
    message_id = data.get("id")
    author = data.get("author")
    if not isinstance(content, str) or not isinstance(message_id, str):
        raise QQProtocolError("QQ message event is missing its content")
    if not isinstance(author, dict):
        raise QQProtocolError("QQ message event is missing its author")

    group_id: str | None = None
    if event_type == GROUP_MESSAGE:
        group_id = data.get("group_openid")
        user_id = author.get("member_openid")
        if not isinstance(group_id, str) or not isinstance(user_id, str):
            raise QQProtocolError("QQ group message has no group or member")
    else:
        user_id = author.get("user_openid")
        if not isinstance(user_id, str):
            raise QQProtocolError("QQ direct message has no open identifier")

    timestamp = data.get("timestamp")
    event_id = payload.get("id")
    return QQMessageEvent(
        connection_id=connection_id,
        user_id=user_id,
        user_name=None,
        group_id=group_id,
        message_id=message_id,
        event_id=event_id if isinstance(event_id, str) else message_id,
        timestamp=timestamp if isinstance(timestamp, int) else None,
        content=content,
        raw=payload,
    )
