"""Bind protocol-owned resources to framework lifecycle phases."""

import sys

from opsmesh import on_quiescing, on_shutdown, on_started, on_startup

from ..config import protocol_settings
from .transports import ChannelTransports

transports = ChannelTransports()


@on_startup
async def prepare() -> None:
    """Prepare the HTTP listener only when configured or declared."""
    entrances = ()
    if (
        protocol_settings("api") is not None
        or "opsmesh.plugins.channels.api" in sys.modules
    ):
        from ..api.channel import APIChannel

        entrance = APIChannel.from_config()
        entrances = (entrance,) if entrance is not None else ()
    await transports.startup(*entrances)


@on_started
async def start() -> None:
    """Open network inputs after consumer readiness."""
    await transports.start_accepting()


@on_quiescing
async def quiesce() -> None:
    """Drain accepted events before consumer resources close."""
    await transports.stopping()


@on_shutdown
async def close() -> None:
    """Release every protocol resource, including failed startups."""
    await transports.shutdown()
