"""Coordinate channel phases without owning protocol connections."""

from __future__ import annotations

import sys

from .transports import ChannelTransports


class ChannelLifecycle:
    """Order declared channel lifecycles around consumer readiness."""

    def __init__(self) -> None:
        """Create an empty set of channel declarations."""
        self.transports = ChannelTransports()

    async def prepare(self) -> None:
        """Configure requested protocols; each owns its connection resources."""
        from ..config import protocol_settings

        entrance = None
        if (
            protocol_settings("api") is not None
            or "opsmesh.plugins.channels.api" in sys.modules
        ):
            from ..api.channel import APIChannel

            entrance = APIChannel.from_config()
        await self.transports.startup(
            *(() if entrance is None else (entrance,))
        )

    async def start(self) -> None:
        """Allow each prepared protocol to open its inputs."""
        await self.transports.start_accepting()

    async def quiesce(self) -> None:
        """Stop and drain protocol inputs before consumers close."""
        await self.transports.stopping()

    async def close(self) -> None:
        """Release all protocol-owned resources."""
        await self.transports.shutdown()


channel_lifecycle = ChannelLifecycle()
