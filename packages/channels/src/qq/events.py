"""QQ official bot message segments and callback events."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict, Field
from qqbot_agent_sdk import EventParser

from opsmesh.adapters import Event

from ..attachments import AttachmentCollection
from ..attachments.transfer import AttachmentReader
from ..messages import Message
from .attachments import parse_message
from .errors import QQProtocolError

C2C_MESSAGE = "C2C_MESSAGE_CREATE"
GROUP_MESSAGE = "GROUP_AT_MESSAGE_CREATE"
GROUP_MESSAGE_CREATE = "GROUP_MESSAGE_CREATE"
GROUP_JOIN_REQUEST = "GROUP_JOIN_REQUEST"
MESSAGE_EVENTS = frozenset({C2C_MESSAGE, GROUP_MESSAGE, GROUP_MESSAGE_CREATE})
NOTICE_EVENTS = frozenset(
    {
        "GUILD_CREATE",
        "GUILD_UPDATE",
        "GUILD_DELETE",
        "GUILD_MEMBER_ADD",
        "GUILD_MEMBER_UPDATE",
        "GUILD_MEMBER_REMOVE",
        "GROUP_ADD_ROBOT",
        "GROUP_DEL_ROBOT",
        "GROUP_MSG_REJECT",
        "GROUP_MSG_RECEIVE",
        GROUP_JOIN_REQUEST,
    }
)


class QQNoticeEvent(Event):
    """A verified platform notice with optional actor information."""

    connection_id: str
    event_id: str
    event_type: str
    guild_id: str | None = None
    group_id: str | None = None
    operator_id: str | None = None
    subject_id: str | None = None
    raw: dict[str, Any]

    @property
    def message_id(self) -> str:
        """Use the delivery ID for the shared queue's diagnostics."""
        return self.event_id

    def get_type(self) -> str:
        """Select framework notice matchers."""
        return "notice"

    def get_event_name(self) -> str:
        """Preserve the platform's exact notice type."""
        return f"qq.notice.{self.event_type}"

    def get_event_description(self) -> str:
        """Describe delivery without logging the callback body."""
        return f"notice {self.event_id} ({self.event_type})"

    def get_user_id(self) -> str:
        """Expose the actual actor only when the platform identifies one."""
        if self.operator_id is None:
            raise ValueError("This notice does not identify an actor")
        return f"{self.connection_id}:{self.operator_id}"

    def get_session_id(self) -> str:
        """Keep guild IDs distinct from ordinary group openids."""
        if self.group_id is not None:
            return f"{self.connection_id}:group:{self.group_id}"
        if self.guild_id is not None:
            return f"{self.connection_id}:guild:{self.guild_id}"
        raise ValueError("This notice has no conversation")

    def get_message(self) -> Message:
        """Reject message-only dependencies for notice events."""
        raise ValueError("Notices do not contain framework messages")

    def is_tome(self) -> bool:
        """The authenticated subscription explicitly targets this bot."""
        return True


class QQMessageEvent(Event):
    """A QQ direct or group message callback."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    connection_id: str
    user_id: str
    user_name: str | None = None
    group_id: str | None = None
    message_id: str
    event_id: str
    timestamp: str | int | None = None
    content: str
    message: Message | None = None
    attachments: AttachmentCollection = Field(
        default_factory=AttachmentCollection, exclude=True
    )
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
        return json.dumps(
            [
                "qq",
                self.connection_id,
                "group" if self.group_id else "private",
                self.group_id,
                self.user_id,
            ],
            separators=(",", ":"),
        )

    def get_message(self) -> Message:
        """Return the message content as QQ segments."""
        return (
            self.message if self.message is not None else Message(self.content)
        )

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
    payload: dict[str, Any],
    *,
    connection_id: str,
    attachments: AttachmentCollection | None = None,
    reader: AttachmentReader | None = None,
) -> QQMessageEvent | QQNoticeEvent | None:
    """Convert one dispatched QQ callback into a framework event.

    Args:
        payload: The verified callback body.
        connection_id: The owning connection identifier.
        attachments: Bounded collection belonging to this incoming event.
        reader: Authenticated protocol media reader, called only on demand.

    Returns:
        A message event, or ``None`` for event types this plugin ignores.

    Raises:
        QQProtocolError: The body is a message event without required fields.
    """
    event_type = payload.get("t")
    if event_type in NOTICE_EVENTS:
        data = payload.get("d")
        if not isinstance(data, dict):
            raise QQProtocolError("QQ notice is missing its data or event ID")
        event_id = payload.get("id")
        if not isinstance(event_id, str) or not event_id:
            sequence = payload.get("s")
            if isinstance(sequence, (str, int)) and not isinstance(
                sequence, bool
            ):
                event_id = f"{event_type}:{sequence}"
            else:
                event_id = data.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise QQProtocolError("QQ notice is missing its event ID")
        operator = data.get(
            "op_member_openid",
            data.get("op_user_id", data.get("member_openid")),
        )
        user = data.get("user")
        subject = data.get("member_openid", data.get("user_openid"))
        subject_id = subject if isinstance(subject, str) else None
        if subject_id is None and isinstance(user, dict):
            candidate = user.get("id")
            subject_id = candidate if isinstance(candidate, str) else None
        return QQNoticeEvent(
            connection_id=connection_id,
            event_id=event_id,
            event_type=event_type,
            guild_id=data.get(
                "guild_id",
                data.get("id")
                if event_type
                in {"GUILD_CREATE", "GUILD_UPDATE", "GUILD_DELETE"}
                else None,
            ),
            group_id=data.get("group_openid"),
            operator_id=operator if isinstance(operator, str) else None,
            subject_id=subject_id,
            raw=payload,
        )
    if event_type not in MESSAGE_EVENTS:
        return None
    data = payload.get("d")
    if not isinstance(data, dict):
        raise QQProtocolError("QQ message event has no data object")
    content = data.get("content", "")
    message_id = data.get("id")
    author = data.get("author")
    if not isinstance(content, str) or not isinstance(message_id, str):
        raise QQProtocolError("QQ message event is missing its content")
    if not isinstance(author, dict):
        raise QQProtocolError("QQ message event is missing its author")

    # SDK 1.2.2 omits GROUP_MESSAGE_CREATE from its parser dispatch table.
    parser_type = (
        GROUP_MESSAGE if event_type == GROUP_MESSAGE_CREATE else event_type
    )
    try:
        native = EventParser.parse(parser_type, data)
    except (TypeError, ValueError, AttributeError):
        raise QQProtocolError("QQ message data is invalid") from None
    if native is None or not native.user_id or not native.chat_id:
        raise QQProtocolError("QQ message has no conversation or user")
    group_id = native.chat_id if native.chat_scope == "group" else None
    user_id = native.user_id

    attachments = attachments or AttachmentCollection()
    message = parse_message(data, attachments, reader)
    timestamp = data.get("timestamp")
    if not isinstance(timestamp, (str, int)):
        timestamp = None
    message_sequence = data.get("msg_seq")
    if isinstance(message_sequence, (str, int)):
        event_id = f"{message_id}:{message_sequence}"
    else:
        event_id = payload.get("id")
    user_name = author.get("username")
    return QQMessageEvent(
        connection_id=connection_id,
        user_id=user_id,
        user_name=user_name if isinstance(user_name, str) else None,
        group_id=group_id,
        message_id=message_id,
        event_id=event_id if isinstance(event_id, str) else message_id,
        timestamp=timestamp,
        content=content,
        message=message,
        attachments=attachments,
        raw=payload,
    )
