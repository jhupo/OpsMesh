"""Common inbound message event shared by every channel protocol."""

from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, model_validator

from opsmesh.adapters import Event

from .attachments import AttachmentCollection
from .context import UserContext, encode_scope
from .messages import Message


class MessageEvent(Event):
    """A normalized message delivered to framework matchers."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    EVENT_NAME: ClassVar[str] = ""

    connection_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    user: UserContext
    conversation_id: str = Field(min_length=1)
    conversation_type: Literal["private", "group"]
    group_id: str | None = None
    message: Message
    attachments: AttachmentCollection = Field(
        default_factory=AttachmentCollection,
        exclude=True,
    )
    to_me: bool

    @model_validator(mode="after")
    def validate_group(self) -> "MessageEvent":
        """Keep the group identifier consistent with the conversation type."""
        if self.conversation_type == "group":
            if not self.group_id:
                raise ValueError("Group messages require a group identifier")
        elif self.group_id is not None:
            raise ValueError("Private messages cannot carry a group identifier")
        return self

    def get_type(self) -> str:
        """Route every protocol message through the framework message type."""
        return "message"

    def get_event_name(self) -> str:
        """Return the fixed protocol message event name."""
        return self.EVENT_NAME

    def get_event_description(self) -> str:
        """Identify the delivery without exposing its content."""
        return f"message {self.message_id} on {self.connection_id}"

    def get_user_id(self) -> str:
        """Return the connection-scoped caller identifier."""
        return self.user.id

    def get_session_id(self) -> str:
        """Isolate continuations by connection, conversation, and caller."""
        return encode_scope(
            self.connection_id,
            self.conversation_type,
            self.conversation_id,
            self.user.id,
        )

    def get_message(self) -> Message:
        """Return the parsed message."""
        return self.message

    def is_tome(self) -> bool:
        """Return whether the message was explicitly directed here."""
        return self.to_me
