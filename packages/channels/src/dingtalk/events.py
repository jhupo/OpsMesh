"""DingTalk robot message segments and callback events."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from opsmesh.adapters import Event

from ..attachments import AttachmentCollection, AttachmentError
from ..attachments.transfer import AttachmentReader
from ..messages import Message
from .attachments import parse_message
from .errors import DingTalkProtocolError


class DingTalkNoticeEvent(Event):
    """An official Stream EVENT, independent of chatbot CALLBACK messages."""

    connection_id: str
    event_id: str
    event_type: str
    open_conversation_id: str | None = None
    chat_id: str | None = None
    operator_id: str | None = None
    raw: dict[str, Any]

    @property
    def message_id(self) -> str:
        """Use the delivery ID for the shared queue's diagnostics."""
        return self.event_id

    def get_type(self) -> str:
        """Select framework notice matchers."""
        return "notice"

    def get_event_name(self) -> str:
        """Preserve the native Stream event type."""
        return f"dingtalk.notice.{self.event_type}"

    def get_event_description(self) -> str:
        """Describe delivery without exposing the native event body."""
        return f"notice {self.event_id} ({self.event_type})"

    def get_user_id(self) -> str:
        """Expose an actor only if one was supplied by the platform."""
        if self.operator_id is None:
            raise ValueError("This notice does not identify an actor")
        return f"{self.connection_id}:{self.operator_id}"

    def get_session_id(self) -> str:
        """Return a conversation only for events that identify one."""
        if self.open_conversation_id is not None:
            return (
                f"{self.connection_id}:open_conversation:"
                f"{self.open_conversation_id}"
            )
        if self.chat_id is not None:
            return f"{self.connection_id}:chat:{self.chat_id}"
        raise ValueError("This notice has no conversation")

    def get_message(self) -> Message:
        """Reject message-only dependencies for notice events."""
        raise ValueError("Notices do not contain framework messages")

    def is_tome(self) -> bool:
        """The authenticated Stream subscription targets this application."""
        return True


class DingTalkMessageEvent(Event):
    """A DingTalk robot message callback."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

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
    message: Message | None = None
    attachments: AttachmentCollection = Field(
        default_factory=AttachmentCollection, exclude=True
    )
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

    def get_message(self) -> Message:
        """Return the message content as DingTalk segments."""
        return (
            self.message if self.message is not None else Message(self.content)
        )

    def is_tome(self) -> bool:
        """Whether the robot was addressed in this group or direct chat."""
        return self.to_me

    @property
    def group_ids(self) -> tuple[str, ...]:
        """Return verified group identifiers for the caller mapping."""
        if self.conversation_type != "2":
            return ()
        return (f"{self.connection_id}:conversation:{self.conversation_id}",)


def parse_callback(
    payload: dict[str, Any],
    *,
    connection_id: str,
    attachments: AttachmentCollection | None = None,
    reader: AttachmentReader | None = None,
) -> DingTalkMessageEvent | None:
    """Convert one verified DingTalk callback into a framework event.

    Args:
        payload: The verified callback body.
        connection_id: The owning connection identifier.
        attachments: Bounded collection belonging to this incoming event.
        reader: Authenticated protocol media reader, called only on demand.

    Returns:
        A message event, or ``None`` for message types this plugin ignores.

    Raises:
        DingTalkProtocolError: Required message fields are missing.
    """
    attachments = attachments or AttachmentCollection()
    try:
        message = parse_message(payload, attachments, reader)
    except AttachmentError as error:
        raise DingTalkProtocolError(str(error)) from None
    if message is None:
        return None
    content = message.extract_plain_text()
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
        message=message,
        attachments=attachments,
        session_webhook=(
            session_webhook if isinstance(session_webhook, str) else None
        ),
        session_webhook_expires_at=(
            expires if isinstance(expires, int) else None
        ),
        to_me=(conversation_type != "2" or bool(payload.get("isInAtList"))),
        raw=payload,
    )
