"""Retain verified callback ownership until dispatch resources close."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio

from opsmesh.adapters import Event
from opsmesh.dispatch import dispatch_event
from opsmesh.plugins.invocation import (
    PluginCall,
    PluginUnavailable,
    hold_plugin,
    invocation_scope,
)
from opsmesh.plugins.registry import plugin_scope

from ..events import MessageEvent
from .queue import EventQueue

if TYPE_CHECKING:
    from ..channel import Channel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedEvent:
    """A verified event with transferable plugin and dependency admission."""

    channel: Channel
    event: Event
    generation: int = field(init=False)
    stack: AsyncExitStack = field(default_factory=AsyncExitStack, init=False)
    admitted_call: PluginCall | None = field(init=False)

    def __post_init__(self) -> None:
        """Retain the receiving plugin activation before admission."""
        owner = self.channel.owner
        object.__setattr__(self, "generation", owner.generation)
        # Each delivery outlives its receiving SDK or HTTP callback task.
        with invocation_scope(None):
            call = hold_plugin(owner, self.stack)
        object.__setattr__(self, "admitted_call", call)

    @property
    def expired(self) -> bool:
        """Whether a different activation now owns this connection."""
        return self.channel.owner.generation != self.generation

    async def close(self) -> None:
        """Release admission after the worker closes dispatch dependencies."""
        with (
            anyio.CancelScope(shield=True),
            plugin_scope(self.channel.owner),
            invocation_scope(self.admitted_call),
        ):
            await self.stack.aclose()


def _invocation_context(event: Event):
    """Messages carry the caller profile; notices have no verified user."""
    return event.user if isinstance(event, MessageEvent) else None


async def dispatch_queued(item: QueuedEvent) -> None:
    """Dispatch one admitted bot event through the framework exactly once."""
    channel = item.channel
    if item.expired or not channel.replies_available:
        logger.info("Cancelled queued event: owner stopped")
        return
    with invocation_scope(item.admitted_call):
        result = await dispatch_event(
            channel,
            item.event,
            invocation_context=_invocation_context(item.event),
            matcher_filter=channel.accepts_matcher,
            admitted_call=item.admitted_call,
        )
    if result.errors:
        logger.error(
            "Channel event dispatch produced %d error(s)", len(result.errors)
        )


async def enqueue(
    queue: EventQueue,
    channel: Channel,
    event: Event,
    *,
    delivery_id: str,
) -> str:
    """Deduplicate and retain admission until dispatch or queue rejection."""
    if not queue.running:
        return "stopped"
    if not channel.first_delivery(delivery_id):
        return "duplicate"
    try:
        item = QueuedEvent(channel, event)
    except PluginUnavailable:
        channel.release_delivery(delivery_id)
        return "plugin unavailable"
    accepted = False
    try:
        accepted = queue.put_nowait(item)
    finally:
        if not accepted:
            channel.release_delivery(delivery_id)
            await item.close()
    if not accepted:
        logger.warning(
            "Rejecting callback for %s: queue is full", channel.self_id
        )
    return "ok" if accepted else "queue is full"
