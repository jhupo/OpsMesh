"""Logical, non-network events for scheduled framework handlers."""

from datetime import datetime
from typing import Literal

from pydantic import JsonValue

from opsmesh.events import Event


class ScheduledEvent(Event):
    """A timer firing without an invented human identity."""

    schedule_id: str
    invocation_id: str
    owner_plugin: str
    name: str
    trigger_type: Literal["cron", "interval", "date"]
    fired_at: datetime
    payload: dict[str, JsonValue]

    def get_type(self) -> str:
        """Return the framework scheduled-event family."""
        return "scheduled"

    def get_event_name(self) -> str:
        """Return the display name of the persisted schedule."""
        return self.name

    def get_event_description(self) -> str:
        """Describe the trigger without including its payload."""
        return f"Schedule {self.name} ({self.schedule_id})"

    def get_user_id(self) -> str:
        """Reject human-user injection into timer work."""
        raise ValueError("Scheduled events do not have a human user")

    def get_session_id(self) -> str:
        """Reject conversation routing for timer events."""
        raise ValueError("Scheduled events do not have a conversation")

    def get_message(self):
        """Reject message injection for timer events."""
        raise ValueError("Scheduled events do not contain messages")

    def is_tome(self) -> bool:
        """Report no addressed human conversation."""
        return False
