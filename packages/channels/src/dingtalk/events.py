"""DingTalk robot message segments and callback events."""

from __future__ import annotations

from typing import Any

from opsmesh.events import Event

from ..attachments import AttachmentCollection, AttachmentError
from ..attachments.transfer import AttachmentReader
from ..context import UserContext, encode_scope
from ..events import MessageEvent
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
        return encode_scope(self.operator_id, self.connection_id)

    def get_session_id(self) -> str:
        """Return a conversation only for events that identify one."""
        if self.open_conversation_id is not None:
            return encode_scope(
                self.connection_id,
                "open_conversation",
                self.open_conversation_id,
            )
        if self.chat_id is not None:
            return encode_scope(self.connection_id, "chat", self.chat_id)
        raise ValueError("This notice has no conversation")

    def get_message(self) -> Message:
        """Reject message-only dependencies for notice events."""
        raise ValueError("Notices do not contain framework messages")

    def is_tome(self) -> bool:
        """The authenticated Stream subscription targets this application."""
        return True


class DingTalkMessageEvent(MessageEvent):
    """A DingTalk robot message callback."""

    EVENT_NAME = "dingtalk.message"

    user_id: str
    user_name: str | None = None
    conversation_title: str | None = None
    session_webhook: str | None = None
    session_webhook_expires_at: int | None = None
    raw: dict[str, Any]


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

    session_webhook = payload.get("sessionWebhook")
    expires = payload.get("sessionWebhookExpiredTime")
    title = payload.get("conversationTitle")
    nickname = payload.get("senderNick")
    nickname = nickname if isinstance(nickname, str) else None
    is_group = payload.get("conversationType") == "2"
    return DingTalkMessageEvent(
        connection_id=connection_id,
        message_id=message_id,
        user=UserContext.scoped(
            connection_id,
            user_id,
            name=nickname or "",
        ),
        user_id=user_id,
        user_name=nickname,
        conversation_id=conversation_id,
        conversation_type="group" if is_group else "private",
        group_id=conversation_id if is_group else None,
        conversation_title=title if isinstance(title, str) else None,
        message=message,
        attachments=attachments,
        session_webhook=(
            session_webhook if isinstance(session_webhook, str) else None
        ),
        session_webhook_expires_at=(
            expires if isinstance(expires, int) else None
        ),
        to_me=not is_group or bool(payload.get("isInAtList")),
        raw=payload,
    )
