"""Transport-independent event contracts and dispatch context."""

import abc
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from opsmesh.config import Config
from opsmesh.messages.message import Message, MessageSegment


class Event(abc.ABC, BaseModel):
    """Base event interface exposing common event information."""

    model_config = ConfigDict(extra="allow")

    @abc.abstractmethod
    def get_type(self) -> str:
        """Return the event type, usually one of the four built-in types."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_event_name(self) -> str:
        """Return the event name."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_event_description(self) -> str:
        """Return a human-readable description of the event."""
        raise NotImplementedError

    def __str__(self) -> str:
        return f"[{self.get_event_name()}]: {self.get_event_description()}"

    def get_log_string(self) -> str:
        """Return the event's log representation.

        Raises:
            NoLogException: The event should not produce a normal event log.
        """
        return f"[{self.get_event_name()}]: {self.get_event_description()}"

    @abc.abstractmethod
    def get_user_id(self) -> str:
        """Return the subject identifier, usually the user ID."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_session_id(self) -> str:
        """Return a source-scoped identifier for this conversation.

        Include the source namespace so unrelated sources sharing a dispatcher
        do not collide in session guards or matcher continuations.
        Raise NotImplementedError for events without a session.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_message(self) -> Message:
        """Return the event's message content."""
        raise NotImplementedError

    def get_plaintext(self) -> str:
        """Return the plain-text content of the event."""
        return self.get_message().extract_plain_text()

    @abc.abstractmethod
    def is_tome(self) -> bool:
        """Return whether this event is addressed to the receiving identity."""
        raise NotImplementedError


class Reply(Protocol):
    """An awaitable reply operation supplied for one event delivery."""

    def __call__(
        self, message: str | Message | MessageSegment, /, **kwargs: Any
    ) -> Awaitable[Any]:
        """Send a reply and return the delivery owner's result."""
        ...


@dataclass(slots=True)
class _EventBinding:
    config: Config | None
    reply: Reply | None

    @contextmanager
    def bind(self) -> Iterator[None]:
        token = _event_binding.set(self)
        try:
            yield
        finally:
            _event_binding.reset(token)

    async def send(
        self, message: str | Message | MessageSegment, /, **kwargs: Any
    ) -> Any:
        if (reply := self.reply) is None:
            raise RuntimeError("No reply callable is available for this event.")
        return await reply(message, **kwargs)


_event_binding: ContextVar[_EventBinding | None] = ContextVar(
    "event_binding", default=None
)


def get_event_config() -> Config:
    """Return the active dispatch configuration, or the framework defaults."""
    binding = _event_binding.get()
    if binding is not None and binding.config is not None:
        return binding.config
    return Config()


def _get_event_reply() -> Reply:
    binding = _event_binding.get()
    if binding is None or binding.reply is None:
        raise RuntimeError("No reply callable is available for this event.")
    return binding.send


@contextmanager
def _event_scope(
    config: Config | None, reply: Reply | None
) -> Iterator[_EventBinding]:
    binding = _EventBinding(Config() if config is None else config, reply)
    with binding.bind():
        try:
            yield binding
        finally:
            # Copied task contexts still point to this object after exit.
            binding.reply = None
            binding.config = None
