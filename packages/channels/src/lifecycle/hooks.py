"""Bind protocol-owned resources to framework lifecycle phases."""

from opsmesh import on_quiescing, on_shutdown, on_started, on_startup

from .transports import ChannelTransports

transports = ChannelTransports()


@on_startup
async def prepare() -> None:
    """Prepare the transport lifecycle coordinator."""
    await transports.startup()


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
