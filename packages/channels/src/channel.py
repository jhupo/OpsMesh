"""Shared ownership, readiness and deduplication for robot connections."""

from typing import Generic, Protocol, TypeVar

from opsmesh.adapters import Adapter, Channel
from opsmesh.matchers import Matcher
from opsmesh.plugins.model import Plugin

from ._dedupe import EventDeduplicator

CONNECTING = "connecting"
READY = "ready"
FAILED = "failed"
DISCONNECTED = "disconnected"
CLOSED = "closed"


class RobotProtocol(Protocol):
    """Limits required by robot callback admission."""

    max_pending_events: int
    concurrency: int
    shutdown_timeout: float
    dedupe_ttl: float
    dedupe_capacity: int


SettingsT = TypeVar("SettingsT")
ProtocolT = TypeVar("ProtocolT", bound=RobotProtocol)


class RobotChannel(Channel, Generic[SettingsT, ProtocolT]):
    """Bind a robot connection to its declaring plugin."""

    def __init__(
        self,
        adapter: Adapter,
        self_id: str,
        *,
        owner_id: str,
        owner: Plugin | None = None,
        connection_name: str,
        settings: SettingsT,
        protocol: ProtocolT,
    ) -> None:
        """Initialize ownership and callback admission state."""
        super().__init__(adapter, self_id)
        self._owner = owner
        self._owner_id = owner_id
        self._connection_name = connection_name
        self._settings = settings
        self._protocol = protocol
        self._state = CONNECTING
        self._replies_available = False
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
    def settings(self) -> SettingsT:
        """The validated connection settings."""
        return self._settings

    @property
    def protocol(self) -> ProtocolT:
        """The shared protocol settings."""
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
        """Whether the inbound connection is ready for callbacks."""
        return self._state == READY

    @property
    def replies_available(self) -> bool:
        """Whether outbound resources remain usable for admitted work."""
        return self._replies_available

    def mark_ready(self) -> None:
        """Mark the connection usable."""
        self._state = READY
        self._replies_available = True
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
        self._replies_available = False
        self._error = "connection closed"

    def release_delivery(self, event_id: str) -> None:
        """Allow a vendor retry when admission failed before execution."""
        self._dedupe.forget(event_id)

    def first_delivery(self, event_id: str) -> bool:
        """Record an event identifier and report whether it is new."""
        return not self._dedupe.seen(event_id)
