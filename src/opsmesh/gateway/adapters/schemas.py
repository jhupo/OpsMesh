"""Gateway transport values. Plugin task access lives in contracts.gateway."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConnectionState = Literal["connecting", "connected", "reconnecting", "failed", "stopped"]


class EventType(StrEnum):
    MESSAGE_CREATED = "message.created"
    MESSAGE_DELETED = "message.deleted"
    MESSAGE_AUDIT_PASSED = "message.audit.passed"
    MESSAGE_AUDIT_REJECTED = "message.audit.rejected"
    REACTION_ADDED = "reaction.added"
    REACTION_REMOVED = "reaction.removed"
    MEMBER_JOINED = "member.joined"
    MEMBER_UPDATED = "member.updated"
    MEMBER_LEFT = "member.left"
    INTERACTION_CREATED = "interaction.created"
    CONNECTION_READY = "connection.ready"
    CONNECTION_RESUMED = "connection.resumed"
    PLATFORM_EVENT = "platform.event"


@dataclass(frozen=True)
class Event:
    """Normalized inbound event; native payloads remain in data, routing in reply_target.

    Content uses type/data parts. Mentions carry a kind and optional id; platform
    parts carry platform/type/data when no portable representation exists.
    """

    id: str
    type: str
    external_user_id: str | None = None
    conversation_id: str | None = None
    text: str = ""
    is_message: bool = False
    scope: str = "private"
    external_role: str | None = None
    reply_target: dict[str, Any] | None = None
    content: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    platform: str | None = None
    native_type: str | None = None


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    text: str
    reply_to: str | None = None
    scope: str = "private"
    sequence: int = 1
    target: dict[str, Any] | None = None
    content: list[dict[str, Any]] = field(default_factory=list)


EventSink = Callable[[Event], Awaitable[None]]


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parameters: dict[str, Any]
    result: dict[str, Any]
    permission: Literal["admin"] = "admin"
    retry: Literal["never"] = "never"
    concurrency: str = "one queued operation per channel; message delivery may overlap"
    rate_limit: str = "platform-enforced; no automatic retry"
    platform_permissions: str = "requires application scopes granted by the platform"
    receipt: str = "validated platform response; ambiguous failures remain uncertain"


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    implementation: str
    version: str
    conversations: list[str]
    events: list[str]
    message_parts: list[str]
    operations: dict[str, Operation]
    configuration: dict[str, Any] = Field(default_factory=dict)


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["accepted", "pending_audit"] = "accepted"
    value: dict[str, Any]


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: str
    value: Any


class SendMessageParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: dict[str, Any]
    text: str = ""
    content: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    reply_to: str | None = None
    scope: Literal["private", "group"] = "private"
    sequence: int = Field(default=1, ge=1)

    def message(self) -> Message:
        return Message(id="operation", conversation_id="", **self.model_dump())
