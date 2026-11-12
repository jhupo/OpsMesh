"""Permission definitions for event matchers.

Each matcher owns a Permission instance
containing a collection of permission checkers.
A single successful checker is sufficient to allow execution.
"""

from opsmesh.params import EventType
from opsmesh.permissions.base import USER as USER
from opsmesh.permissions.base import Permission as Permission
from opsmesh.permissions.base import User as User


class Message:
    """Check whether the event is a message event."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "Message()"

    async def __call__(self, type: str = EventType()) -> bool:
        """Allow message events."""
        return type == "message"


class Notice:
    """Check whether the event is a notice event."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "Notice()"

    async def __call__(self, type: str = EventType()) -> bool:
        """Allow notice events."""
        return type == "notice"


class Request:
    """Check whether the event is a request event."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "Request()"

    async def __call__(self, type: str = EventType()) -> bool:
        """Allow request events."""
        return type == "request"


class MetaEvent:
    """Check whether the event is a meta event."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "MetaEvent()"

    async def __call__(self, type: str = EventType()) -> bool:
        """Allow meta events."""
        return type == "meta_event"


MESSAGE: Permission = Permission(Message())
"""Match any message event.

Prefer a message-type matcher unless matching multiple event types.
"""
NOTICE: Permission = Permission(Notice())
"""Match any notice event.

Prefer a notice-type matcher unless matching multiple event types.
"""
REQUEST: Permission = Permission(Request())
"""Match any request event.

Prefer a request-type matcher unless matching multiple event types.
"""
METAEVENT: Permission = Permission(MetaEvent())
"""Match any meta_event event.

Prefer a meta_event-type matcher unless matching multiple event types.
"""


__autodoc__ = {
    "Permission": True,
    "Permission.__call__": True,
    "User": True,
    "USER": True,
}
