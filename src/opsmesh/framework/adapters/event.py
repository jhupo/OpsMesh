"""Protocol event contracts; bot, message, user and session are optional."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from opsmesh.framework.exception import ContextUnavailableError

from .message import OpsMeshMessage


class OpsMeshEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str
    id: str = Field(default_factory=lambda: uuid4().hex)
    source: str = "local"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Event timestamps must include a timezone")
        return value.astimezone(UTC)

    @property
    def event_id(self) -> str:
        return self.id

    def get_type(self) -> str:
        return self.type

    def get_event_name(self) -> str:
        return self.type

    def get_event_description(self) -> str:
        return f"{self.type} ({self.id})"

    def get_message(self) -> OpsMeshMessage:
        raise ContextUnavailableError(f"{self.type} has no message")

    def get_plaintext(self) -> str:
        return self.get_message().extract_plain_text()

    def get_user_id(self) -> str:
        raise ContextUnavailableError(f"{self.type} has no user")

    def get_session_id(self) -> str:
        raise ContextUnavailableError(f"{self.type} has no session")

    def is_tome(self) -> bool:
        return False


class MessageEvent(OpsMeshEvent):
    type: str = "message"
    message: OpsMeshMessage
    user_id: str
    session_id: str
    to_me: bool = False

    def get_message(self) -> OpsMeshMessage:
        return self.message

    def get_user_id(self) -> str:
        return self.user_id

    def get_session_id(self) -> str:
        return self.session_id

    def is_tome(self) -> bool:
        return self.to_me


class TimerEvent(OpsMeshEvent):
    type: Literal["timer.fired"] = "timer.fired"
    job_id: str
    scheduled_at: datetime
