"""Trusted invocation values and application-owned authorization extension points."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class InvocationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    channel_id: UUID | None = None
    conversation_id: str | None = None
    identity_id: UUID | None = None
    reply_scope: Literal["private", "group"] = "private"


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
