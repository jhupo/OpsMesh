"""Public conversation contracts, separate from native SDK history."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """Associate text with an owned conversation; omit ID to create one."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=8000)
    session_id: UUID | None = None


class ChatReply(BaseModel):
    """Return the answer and the conversation used for this request."""

    text: str
    session_id: str


class ConversationCreate(BaseModel):
    """Name a new empty conversation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="New conversation", min_length=1, max_length=120)


class ConversationView(BaseModel):
    """Expose only conversation metadata owned by the caller."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    deleting: bool
    created_at: datetime
    updated_at: datetime
