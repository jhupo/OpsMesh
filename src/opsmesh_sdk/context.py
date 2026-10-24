from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .deps import Dependencies
from .events import Events
from .gateway import Channel
from .identity import UserIdentity
from .interactions import Decision
from .resources import Resources
from .sessions import SessionContext


@dataclass(frozen=True)
class InvocationSource:
    channel_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    external_user_id: str | None = None
    external_role: str | None = None
    reply_scope: str = "private"


class Interactions(Protocol):
    async def checkpoint(self, value: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class InvocationContext:
    user: UserIdentity
    task_id: str
    attempt_id: str
    config: Mapping[str, Any]
    resources: Resources
    dependencies: Dependencies
    interactions: Interactions
    events: Events
    session: SessionContext
    metadata: Mapping[str, Any] = field(default_factory=dict)
    source: InvocationSource = field(default_factory=InvocationSource)
    checkpoint: dict[str, Any] | None = None
    decision: Decision | None = None
    channel: Channel | None = None
