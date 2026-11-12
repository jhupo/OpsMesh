"""Event contracts for adapter implementations."""

import abc
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from opsmesh.messages.message import Message

E = TypeVar("E", bound="Event")


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

        The default implementation is usually sufficient. Raise
        NoLogException
        to suppress the normal event log.

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
        """Return the conversation identifier used to group related events.

        It is typically derived from user and group identifiers.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_message(self) -> "Message":
        """Return the event's message content."""
        raise NotImplementedError

    def get_plaintext(self) -> str:
        """Return the plain-text content of the event.

        By default this delegates to get_message().extract_plain_text().
        """
        return self.get_message().extract_plain_text()

    @abc.abstractmethod
    def is_tome(self) -> bool:
        """Return whether this event is directed to the current channel."""
        raise NotImplementedError
