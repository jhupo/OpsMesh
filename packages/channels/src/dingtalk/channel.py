"""DingTalk connection state and outgoing message rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opsmesh.adapters import Channel
from opsmesh.matchers import Matcher
from opsmesh.plugins.channels._dedupe import EventDeduplicator
from opsmesh.plugins.model import Plugin

from .config import DingTalkConnectionSettings, DingTalkSettings
from .events import DingTalkMessageEvent

if TYPE_CHECKING:
    from .adapter import DingTalkAdapter

CONNECTING = "connecting"
READY = "ready"
FAILED = "failed"
DISCONNECTED = "disconnected"
CLOSED = "closed"


class DingTalkChannel(Channel):
    """One declared DingTalk robot connection."""

    receive_types = frozenset({"text", "image", "file", "audio", "video"})
    send_types = receive_types

    @property
    def supported_operations(self) -> frozenset[str]:
        """Return implemented operations, independent of account privileges."""
        from .groups import DingTalkGroups

        return DingTalkGroups.operations | {"send_message", "stream"}

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
    ) -> None:
        """Initialize a declared but not yet active connection."""
        super().__init__(adapter, self_id)
        self._owner = owner
        self._owner_id = owner_id
        self._connection_name = connection_name
        self._settings = settings
        self._protocol = protocol
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
            matcher._source is not None
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
            message: Text or framework message with attachment references.
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
