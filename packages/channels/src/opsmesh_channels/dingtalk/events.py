"""DingTalk robot message segments and callback events."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from opsmesh.framework.adapters import Event
from opsmesh.framework.messages import Message, MessageSegment

from .errors import DingTalkProtocolError


class DingTalkMessageSegment(MessageSegment["DingTalkMessage"]):
    """One DingTalk text segment."""

    @classmethod
    def get_message_class(cls) -> type[DingTalkMessage]:
        """Return the DingTalk message sequence type."""
        return DingTalkMessage

    def __str__(self) -> str:
        """Return the segment text used for command matching."""
        return str(self.data.get("text", ""))

    def is_text(self) -> bool:
        """Whether this segment carries plain text."""
        return self.type == "text"


class DingTalkMessage(Message[DingTalkMessageSegment]):
    """A sequence of DingTalk text segments."""

    @classmethod
    def get_segment_class(cls) -> type[DingTalkMessageSegment]:
        """Return the DingTalk segment type."""
        return DingTalkMessageSegment

    @staticmethod
    def _construct(msg: str) -> list[DingTalkMessageSegment]:
        if not msg:
            return []
        return [DingTalkMessageSegment("text", {"text": msg})]


class DingTalkMessageEvent(Event):
    """A DingTalk robot message callback."""

    model_config = ConfigDict(extra="allow")

    connection_id: str
    user_id: str
    user_name: str | None = None
    conversation_id: str
    conversation_title: str | None = None
    conversation_type: str | None = None
    message_id: str
    content: str
    session_webhook: str | None = None
    session_webhook_expires_at: int | None = None
    to_me: bool = True
    raw: dict[str, Any]

    def get_type(self) -> str:
        """Return the framework message event type."""
        return "message"

    def get_event_name(self) -> str:
        """Return the DingTalk message event name."""
        return "dingtalk.message"

    def get_event_description(self) -> str:
        """Describe the event without logging its message content."""
        return f"message {self.message_id} from {self.get_user_id()}"

    def get_user_id(self) -> str:
        """Return the caller identifier scoped by connection."""
        return f"{self.connection_id}:{self.user_id}"

    def get_session_id(self) -> str:
        """Return the conversation identifier scoped by connection."""
        return f"{self.connection_id}:conversation:{self.conversation_id}"

    def get_message(self) -> DingTalkMessage:
        """Return the message content as DingTalk segments."""
        return DingTalkMessage(self.content)

    def is_tome(self) -> bool:
        """Whether the robot was addressed in this group or direct chat."""
        return self.to_me

    @property
    def group_ids(self) -> tuple[str, ...]:
        """Return verified group identifiers for the caller mapping."""
        if self.conversation_type != "2":
            return ()
        return (f"{self.connection_id}:conversation:{self.conversation_id}",)


def _text_content(payload: dict[str, Any]) -> str | None:
    msgtype = payload.get("msgtype")
    if msgtype == "text":
        body = payload.get("text")
        content = body.get("content") if isinstance(body, dict) else None
    elif msgtype == "markdown":
        body = payload.get("markdown")
        content = body.get("text") if isinstance(body, dict) else None
    else:
        return None
    if not isinstance(content, str):
        raise DingTalkProtocolError(
            "DingTalk message event has no text content"
        )
    return content


def parse_callback(
    payload: dict[str, Any], *, connection_id: str
) -> DingTalkMessageEvent | None:
    """Convert one verified DingTalk callback into a framework event.

    Args:
        payload: The verified callback body.
        connection_id: The owning connection identifier.

    Returns:
        A message event, or ``None`` for message types this plugin ignores.

    Raises:
        DingTalkProtocolError: Required message fields are missing.
    """
    content = _text_content(payload)
    if content is None:
        return None
    conversation_id = payload.get("conversationId")
    message_id = payload.get("msgId")
    user_id = payload.get("senderStaffId") or payload.get("senderId")
    if not isinstance(conversation_id, str) or not isinstance(message_id, str):
        raise DingTalkProtocolError(
            "DingTalk message event is missing its identity"
        )
    if not isinstance(user_id, str) or not user_id:
        raise DingTalkProtocolError(
            "DingTalk message event is missing its sender"
        )

    conversation_type = payload.get("conversationType")
    session_webhook = payload.get("sessionWebhook")
    expires = payload.get("sessionWebhookExpiredTime")
    title = payload.get("conversationTitle")
    nickname = payload.get("senderNick")
    return DingTalkMessageEvent(
        connection_id=connection_id,
        user_id=user_id,
        user_name=nickname if isinstance(nickname, str) else None,
        conversation_id=conversation_id,
        conversation_title=title if isinstance(title, str) else None,
        conversation_type=(
            conversation_type if isinstance(conversation_type, str) else None
        ),
        message_id=message_id,
        content=content,
        session_webhook=(
            session_webhook if isinstance(session_webhook, str) else None
        ),
        session_webhook_expires_at=(
            expires if isinstance(expires, int) else None
        ),
        to_me=(conversation_type != "2" or bool(payload.get("isInAtList"))),
        raw=payload,
    )
