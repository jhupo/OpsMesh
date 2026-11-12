"""DingTalk connection state and outgoing message rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opsmesh.framework.adapters import Channel
from opsmesh.framework.matchers import Matcher
from opsmesh.framework.messages import Message, MessageSegment
from opsmesh.framework.plugins.model import Plugin
from opsmesh_channels._dedupe import EventDeduplicator

from .config import DingTalkConnectionSettings, DingTalkSettings
from .events import (
    DingTalkMessage,
    DingTalkMessageEvent,
    DingTalkMessageSegment,
)

if TYPE_CHECKING:
    from .adapter import DingTalkAdapter

CONNECTING = "connecting"
READY = "ready"
FAILED = "failed"
DISCONNECTED = "disconnected"
CLOSED = "closed"


def outgoing_text(message: Any, limit: int) -> str:
    """Convert a framework message into bounded DingTalk text.

    Args:
        message: A string, DingTalk message, or DingTalk text segment.
        limit: Maximum accepted character count.

    Returns:
        The text payload to send.

    Raises:
        TypeError: The message is not a DingTalk text message.
        ValueError: The message is empty or exceeds the limit.
    """
    if isinstance(message, str):
        text = message
    elif isinstance(message, DingTalkMessageSegment):
        if not message.is_text():
            raise TypeError("DingTalk channels only send text segments")
        text = str(message)
    elif isinstance(message, DingTalkMessage):
        if not all(segment.is_text() for segment in message):
            raise TypeError("DingTalk channels only send text segments")
        text = message.extract_plain_text()
    elif isinstance(message, MessageSegment):
        raise TypeError("DingTalk channels only send DingTalk segments")
    elif isinstance(message, Message):
        raise TypeError("DingTalk channels only send DingTalk messages")
    else:
        raise TypeError("DingTalk channels only send text messages")

    if not text.strip():
        raise ValueError("DingTalk messages cannot be empty")
    if len(text) > limit:
        raise ValueError(f"DingTalk messages are limited to {limit} characters")
    return text


class DingTalkChannel(Channel):
    """One declared DingTalk robot connection."""

    def __init__(
        self,
        adapter: DingTalkAdapter,
        self_id: str,
        *,
        owner_id: str,
        owner: Plugin | None = None,
        connection_name: str,
        settings: DingTalkConnectionSettings,
        protocol: DingTalkSettings,
        path: str,
    ) -> None:
        """Initialize a declared but not yet active connection."""
        super().__init__(adapter, self_id)
        self._owner = owner
        self._owner_id = owner_id
        self._connection_name = connection_name
        self._settings = settings
        self._protocol = protocol
        self._path = path
        self._state = CONNECTING
        self._error: str | None = None
        self._dedupe = EventDeduplicator(
            ttl=protocol.dedupe_ttl, capacity=protocol.dedupe_capacity
        )

    @property
    def owner_ready(self) -> bool:
        """Whether the declaring plugin is ready to accept work."""
        return self._owner is None or self._owner.ready

    def accepts_matcher(self, matcher: type[Matcher]) -> bool:
        """Limit dispatch to handlers owned by the declaring plugin."""
        return bool(
            self.owner_ready
            and matcher._source is not None
            and matcher._source.plugin_id == self.owner_id
        )

    @property
    def owner_id(self) -> str:
        """The plugin installation that declared this connection."""
        return self._owner_id

    @property
    def connection_name(self) -> str:
        """The declaring plugin's connection name."""
        return self._connection_name

    @property
    def settings(self) -> DingTalkConnectionSettings:
        """The validated connection settings."""
        return self._settings

    @property
    def protocol(self) -> DingTalkSettings:
        """The shared DingTalk protocol settings."""
        return self._protocol

    @property
    def path(self) -> str:
        """The callback path registered on the HTTP transport."""
        return self._path

    @property
    def state(self) -> str:
        """The connection state."""
        return self._state

    @property
    def error(self) -> str | None:
        """A client-safe reason when the connection is not ready."""
        return self._error

    @property
    def ready(self) -> bool:
        """Whether callbacks and replies for this connection are available."""
        return self._state == READY

    def mark_ready(self) -> None:
        """Mark the connection usable."""
        self._state = READY
        self._error = None

    def mark_failed(self, reason: str) -> None:
        """Mark the connection unusable with a safe reason."""
        self._state = FAILED
        self._error = reason

    def mark_disconnected(self) -> None:
        """Mark a previously ready connection unavailable."""
        self._state = DISCONNECTED
        self._error = "connection disconnected"

    def mark_closed(self) -> None:
        """Mark the connection permanently closed."""
        self._state = CLOSED
        self._error = "connection closed"

    def release_delivery(self, event_id: str) -> None:
        """Allow a vendor retry when admission failed before execution."""
        self._dedupe.forget(event_id)

    def first_delivery(self, event_id: str) -> bool:
        """Record an event identifier and report whether it is new."""
        return not self._dedupe.seen(event_id)

    async def send(
        self,
        event: DingTalkMessageEvent,
        message: Any,
        **kwargs: Any,
    ) -> Any:
        """Reply to one incoming DingTalk message.

        Args:
            event: The incoming DingTalk message event.
            message: The text content to send.
            **kwargs: Reserved for future send options.

        Returns:
            The vendor response payload.
        """
        if not isinstance(event, DingTalkMessageEvent):
            raise TypeError(
                "DingTalkChannel.send requires a DingTalkMessageEvent"
            )
        return await self.call(
            "send_message", event=event, message=message, **kwargs
        )
