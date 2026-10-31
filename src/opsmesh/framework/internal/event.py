"""Wire envelopes and process-local contexts."""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh.framework.adapters.bot import OpsMeshBot, ReplyTarget
from opsmesh.framework.adapters.event import OpsMeshEvent
from opsmesh.framework.exception import ContextUnavailableError

E = TypeVar("E", bound=OpsMeshEvent)


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    type: str
    schema_version: int = Field(default=1, ge=1)
    occurred_at: datetime
    source: str
    correlation_id: str | None = None
    causation_id: str | None = None
    actor_ref: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    trace_context: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, JsonValue]

    @classmethod
    def from_event(cls, event: OpsMeshEvent, **metadata: Any) -> EventEnvelope:
        return cls(
            id=event.id,
            type=event.type,
            occurred_at=event.occurred_at,
            source=event.source,
            payload=event.model_dump(mode="json"),
            **metadata,
        )

    def decode(self, event_type: builtins.type[E]) -> E:
        if self.schema_version != 1:
            raise ValueError(f"Unsupported event schema version: {self.schema_version}")
        event = event_type.model_validate(self.payload)
        if (event.id, event.type, event.source, event.occurred_at) != (
            self.id,
            self.type,
            self.source,
            self.occurred_at,
        ):
            raise ValueError("Envelope and payload identity mismatch")
        return event


@dataclass(frozen=True)
class DispatchContext(Generic[E]):
    event: E
    bot: OpsMeshBot | None = None
    reply_target: ReplyTarget | None = None
    envelope: EventEnvelope | None = None
    values: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.envelope is not None and (
            self.envelope.id != self.event.id or self.envelope.type != self.event.type
        ):
            raise ValueError("Dispatch event and envelope identity mismatch")
        if (
            self.bot is not None
            and self.reply_target is not None
            and self.bot.ref != self.reply_target.bot
        ):
            raise ValueError("Reply target belongs to a different bot")


_current_context: ContextVar[DispatchContext[OpsMeshEvent]] = ContextVar("opsmesh_dispatch")


def get_current_context() -> DispatchContext[OpsMeshEvent]:
    try:
        return _current_context.get()
    except LookupError as exc:
        raise ContextUnavailableError("No active event dispatch") from exc


def get_current_event() -> OpsMeshEvent:
    return get_current_context().event


def get_current_bot() -> OpsMeshBot:
    bot = get_current_context().bot
    if bot is None:
        raise ContextUnavailableError("This event has no bot")
    return bot


@contextmanager
def bind_context(context: DispatchContext[OpsMeshEvent]) -> Iterator[None]:
    token = _current_context.set(context)
    try:
        yield
    finally:
        _current_context.reset(token)
