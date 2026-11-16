"""Retain verified callback ownership until dispatch resources close."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

import anyio

from opsmesh.adapters import Channel, Event
from opsmesh.dispatch import dispatch_event
from opsmesh.plugins.invocation import PluginCall, hold_plugin, invocation_scope
from opsmesh.plugins.model import Plugin
from opsmesh.plugins.registry import plugin_scope

from .context import UserContext


@dataclass(frozen=True)
class QueuedEvent:
    """A verified event with transferable plugin and dependency admission."""

    channel: Channel
    event: Event
    caller: UserContext | None
    generation: int = field(init=False)
    stack: AsyncExitStack = field(default_factory=AsyncExitStack, init=False)
    admitted_call: PluginCall | None = field(init=False)

    def __post_init__(self) -> None:
        owner = self.channel._owner
        plugin = owner if isinstance(owner, Plugin) else None
        object.__setattr__(
            self, "generation", plugin.generation if plugin else 0
        )
        # Each delivery outlives its receiving SDK or HTTP callback task.
        with invocation_scope(None):
            call = hold_plugin(plugin, self.stack)
        object.__setattr__(self, "admitted_call", call)

    @property
    def expired(self) -> bool:
        """Whether a different activation now owns this connection."""
        owner = self.channel._owner
        return isinstance(owner, Plugin) and owner.generation != self.generation

    async def close(self) -> None:
        """Release admission after the worker closes dispatch dependencies."""
        owner = self.channel._owner
        plugin = owner if isinstance(owner, Plugin) else None
        with (
            anyio.CancelScope(shield=True),
            plugin_scope(plugin),
            invocation_scope(self.admitted_call),
        ):
            await self.stack.aclose()


async def dispatch_queued(item: QueuedEvent) -> None:
    """Dispatch one admitted bot event through the framework exactly once."""
    if item.expired or not item.channel.ready:
        logging.getLogger(__name__).info(
            "Cancelled queued event: owner stopped"
        )
        return
    with invocation_scope(item.admitted_call):
        result = await dispatch_event(
            item.channel,
            item.event,
            invocation_context=item.caller,
            matcher_filter=item.channel.accepts_matcher,
            admitted_call=item.admitted_call,
        )
    if result.errors:
        logging.getLogger(__name__).error(
            "Channel event dispatch produced %d error(s)", len(result.errors)
        )
