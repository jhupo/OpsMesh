"""Trusted invocation values and application-owned authorization extension points."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    message_id: str = Field(min_length=1, max_length=500)
    identity_id: UUID
    external_id: str = Field(min_length=1, max_length=500)
    external_role: str | None = Field(default=None, max_length=64)


class InvocationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    channel_id: UUID | None = None
    receipt_id: UUID | None = None
    conversation_id: str | None = None
    identity_id: UUID | None = None
    reply_scope: Literal["private", "group"] = "private"
    external_role: str | None = Field(default=None, max_length=64)
    external_id: str | None = Field(default=None, max_length=500)
    identity_scope: str | None = Field(default=None, max_length=300)
    session_scope: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    stream_output: bool = False
    participants: list[MessageParticipant] = Field(default_factory=list, max_length=100)


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: UUID
    roles: frozenset[str] = frozenset()
    group_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AuthorizationRequest:
    principal: Principal
    action: Literal["discover", "execute"]
    resource: str
    source: InvocationSource = field(default_factory=InvocationSource)
    arguments: Mapping[str, object] = field(default_factory=dict, repr=False)


ToolAuthorizer = Callable[[AuthorizationRequest], bool | Awaitable[bool]]
