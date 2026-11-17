"""Bind fixed message endpoints to plugin-owned framework matchers."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from opsmesh.dependencies import Dependent
from opsmesh.dependencies.context import ContextParam
from opsmesh.matchers import Matcher
from opsmesh.params import ChannelParam, DefaultParam, DependParam
from opsmesh.plugins.model import Plugin
from opsmesh.plugins.registry import get_current_plugin, track_registration

from .._validation import connection_segment
from ..context import UserContext
from .errors import EntranceError


@dataclass(frozen=True)
class MessageEntrance:
    """One plugin's handlers and identity resolver for a named connection."""

    owner: Plugin
    message_handlers: tuple[type[Matcher], ...]
    identity: Dependent[UserContext]
    connection: str
    name: str

    @property
    def installation_id(self) -> str:
        """Identify the installation without copying its lifecycle state."""
        return self.owner.id_

    @property
    def path(self) -> str:
        """Return the fixed message endpoint for this installation."""
        return f"/plugins/{self.installation_id}/messages"

    @property
    def active(self) -> bool:
        """Read readiness from the framework plugin."""
        return self.owner.ready

    def accepts_matcher(self, matcher: type[Matcher]) -> bool:
        """Include bound handlers and continuations owned by those handlers."""
        return matcher in self.message_handlers or (
            matcher.temp and matcher.origin in self.message_handlers
        )


class MessageEntrances:
    """Track message bindings; FastAPI owns all HTTP routing."""

    def __init__(self) -> None:
        """Create an empty set of plugin bindings."""
        self._entries: dict[tuple[str, str], MessageEntrance] = {}

    @property
    def entries(self) -> tuple[MessageEntrance, ...]:
        """Return the current plugin bindings."""
        return tuple(self._entries.values())

    def get(
        self, plugin_id: str, connection: str = "main"
    ) -> MessageEntrance | None:
        """Look up an entrance without activating its plugin."""
        return self._entries.get((plugin_id, connection))

    def bind(
        self,
        *,
        handlers: tuple[type[Matcher], ...],
        identity: Callable[..., Any],
        connection: str = "main",
        name: str | None = None,
    ) -> MessageEntrance:
        """Bind existing matchers within the declaring plugin scope."""
        owner = get_current_plugin()
        if owner is None:
            raise EntranceError("A message entrance requires a loaded plugin")
        connection_segment(owner.id_, "Plugin installation ID")
        connection_segment(connection, "Connection name")
        if not handlers:
            raise EntranceError("Message entrances require handlers")
        for matcher in handlers:
            if not isinstance(matcher, type) or not issubclass(
                matcher, Matcher
            ):
                raise EntranceError("Expected a framework Matcher")
            if matcher.plugin_id != owner.id_:
                raise EntranceError(
                    "Message handlers must belong to the plugin"
                )
            if not matcher.block or matcher.temp:
                raise EntranceError(
                    "Message handlers must be blocking and persistent"
                )
        if len({matcher.priority for matcher in handlers}) != len(handlers):
            raise EntranceError("Message handlers require distinct priorities")
        key = (owner.id_, connection)
        if key in self._entries:
            raise EntranceError("Plugin message entrance is already registered")
        entrance = MessageEntrance(
            owner,
            tuple(handlers),
            Dependent.parse(
                call=identity,
                allow_types=(
                    ContextParam,
                    DependParam,
                    ChannelParam,
                    DefaultParam,
                ),
            ),
            connection,
            name or f"{owner.id_}.messages",
        )
        self._entries[key] = entrance

        def remove() -> None:
            if self._entries.get(key) is entrance:
                del self._entries[key]

        track_registration(remove)
        return entrance


_entrances = MessageEntrances()


def get_entrances() -> MessageEntrances:
    """Return the plugin's message bindings."""
    return _entrances


def message(
    *,
    handlers: tuple[type[Matcher], ...],
    identity: Callable[..., Any],
    connection: str = "main",
    name: str | None = None,
) -> MessageEntrance:
    """Expose existing framework handlers through the fixed message protocol."""
    return _entrances.bind(
        handlers=handlers, identity=identity, connection=connection, name=name
    )
