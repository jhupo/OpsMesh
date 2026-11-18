"""Caller profile supplied by a channel protocol."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opsmesh.events import Event
from opsmesh.exceptions import SkippedException

if TYPE_CHECKING:
    from .channel import Channel


@dataclass(slots=True)
class _EventChannel:
    value: tuple[Event, Channel] | None


_event_channel: ContextVar[_EventChannel | None] = ContextVar(
    "_event_channel", default=None
)


@contextmanager
def bind_event_channel(event: Event, channel: Channel) -> Iterator[None]:
    """Expose one channel during event dispatch and dependency cleanup."""
    binding = _EventChannel((event, channel))
    token = _event_channel.set(binding)
    try:
        yield
    finally:
        # Child tasks inherit the binding but may outlive its owner.
        binding.value = None
        _event_channel.reset(token)


def channel_for_event(event: Event) -> Channel | None:
    """Look up a channel without skipping when the event has no binding."""
    binding = _event_channel.get()
    pair = binding.value if binding is not None else None
    return pair[1] if pair is not None and pair[0] is event else None


def get_event_channel(event: Event) -> Channel:
    """Inject the plugin-owned channel only for the exact dispatched event."""
    channel = channel_for_event(event)
    if channel is None:
        raise SkippedException("Event is not bound to a channel")
    return channel


def encode_scope(*parts: str) -> str:
    """Encode identifier parts without delimiter ambiguity."""
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class UserContext:
    """A protocol-provided profile, not a verified principal."""

    id: str
    name: str = ""
    groups: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()

    @classmethod
    def scoped(
        cls,
        connection_id: str,
        user_id: str,
        *,
        name: str = "",
        groups: Iterable[str] = (),
        roles: Iterable[str] = (),
    ) -> UserContext:
        """Key a native user identifier to the connection that named it."""
        return cls(
            id=encode_scope(user_id, connection_id),
            name=name,
            groups=frozenset(groups),
            roles=frozenset(roles),
        )

    def __post_init__(self) -> None:
        """Validate profile fields and freeze user-owned collections."""
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("User id must be a nonempty string")
        if not isinstance(self.name, str):
            raise TypeError("User name must be a string")
        for field in ("groups", "roles"):
            values = getattr(self, field)
            if isinstance(values, str):
                raise TypeError(f"User {field} must be a collection")
            frozen = frozenset(values)
            if any(not isinstance(value, str) or not value for value in frozen):
                raise ValueError(f"User {field} must contain nonempty strings")
            object.__setattr__(self, field, frozen)
