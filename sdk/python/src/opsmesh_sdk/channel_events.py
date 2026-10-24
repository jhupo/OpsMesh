"""Non-chat channel events and pure event policy registrations."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from opsmesh_sdk.channel_management import (
    READ_ACTIONS,
    ConversationRef,
    ManagementRequest,
    ManagementValue,
)


class ConversationEvent(ManagementValue):
    event_id: str = Field(min_length=1, max_length=500)
    kind: Literal["member_joined", "member_left", "join_requested", "permissions_changed"]
    conversation: ConversationRef
    member_id: str | None = Field(default=None, min_length=1, max_length=500)
    request_id: str | None = Field(default=None, min_length=1, max_length=2000)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Event time requires a timezone")
        return value

    @model_validator(mode="after")
    def validate_subject(self) -> "ConversationEvent":
        if self.kind != "permissions_changed" and self.member_id is None:
            raise ValueError("Member events require a member reference")
        if self.kind == "join_requested" and self.request_id is None:
            raise ValueError("Join events require an application reference")
        return self


class EventDecision(ManagementValue):
    operations: list[ManagementRequest] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_effects(self) -> "EventDecision":
        if any(item.action in READ_ACTIONS for item in self.operations):
            raise ValueError("Event decisions contain only management effects")
        return self


EventHandler = Callable[[ConversationEvent], Awaitable[EventDecision]]


@dataclass(frozen=True)
class EventHandlerRegistration:
    handler: EventHandler
    timeout_seconds: float = 10
