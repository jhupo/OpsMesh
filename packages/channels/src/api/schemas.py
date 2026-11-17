"""The fixed request contract accepted by every HTTP message endpoint."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ..context import UserContext


class UserProfile(BaseModel):
    """A profile declared by the requesting client, not a verified identity."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=128)
    groups: list[str] = Field(default_factory=list, max_length=64)
    roles: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("groups", "roles")
    @classmethod
    def nonempty_collection(cls, value: list[str]) -> list[str]:
        """Reject empty or oversized membership names in place."""
        if any(
            not isinstance(item, str) or not 1 <= len(item) <= 128
            for item in value
        ):
            raise ValueError("Membership names must be 1-128 characters")
        return value

    def to_context(self, connection_id: str) -> UserContext:
        """Key the declared identifier to the connection that received it."""
        return UserContext.scoped(
            connection_id,
            self.id,
            name=self.name,
            groups=self.groups,
            roles=self.roles,
        )


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
    user: UserProfile
    message: MessageInput
    conversation_type: Literal["private", "group"] = "private"
    conversation_id: str | None = None
    group_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_conversation_scope(self) -> "MessageRequest":
        """Require a group identifier only for group conversations."""
        if self.conversation_type == "group" and (
            self.group_id is None or not self.group_id.strip()
        ):
            raise ValueError("group conversations require group_id")
        if self.conversation_type == "private" and self.group_id is not None:
            raise ValueError("private conversations cannot carry group_id")
        return self

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
