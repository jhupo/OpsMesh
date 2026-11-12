"""Connect channel ownership to protocol-independent framework phases."""

from opsmesh import on_quiescing, on_shutdown, on_started, on_startup

from .coordinator import channel_lifecycle


@on_startup
async def prepare() -> None:
    """Prepare configured entrances and consumer-declared bot adapters."""
    await channel_lifecycle.prepare()


@on_started
async def start() -> None:
    """Open the listener after plugin readiness completes."""
    await channel_lifecycle.start()


@on_quiescing
async def quiesce() -> None:
    """Drain all entrances before consumer stopping callbacks execute."""
    await channel_lifecycle.quiesce()


@on_shutdown
async def close() -> None:
    """Release shared channel resources after consumers close."""
    await channel_lifecycle.close()
