"""Coordinate the configured API and plugin-declared bot transports."""

from typing import Any, Protocol, runtime_checkable

import anyio

from opsmesh.framework import on_shutdown, on_startup, on_stopping
from opsmesh.framework.plugins.registry import track_registration


class ChannelTransport(Protocol):
    """Lifecycle shared by adapters and the API entrance owner."""

    async def startup(self) -> None:
        """Acquire resources before consumers become ready."""

    def stop_accepting(self) -> None:
        """Reject new requests while keeping reply resources available."""

    async def shutdown(self) -> None:
        """Release resources, including partially initialized ones."""


@runtime_checkable
class BufferedTransport(ChannelTransport, Protocol):
    """A transport that owns work beyond the HTTP request lifetime."""

    async def drain(self) -> None:
        """Complete or cancel accepted work before consumers close."""


class ChannelLifecycles:
    """Retain declared transports until their consumers finish using them."""

    def __init__(self) -> None:
        """Create a stopped coordinator without protocol dependencies."""
        self._declared: dict[ChannelTransport, int] = {}
        self._started: list[ChannelTransport] = []
        self._running = False

    def use(self, transport: ChannelTransport, owner: Any = None) -> None:
        """Attach one declaration to the framework's owning plugin scope."""
        if self._running and transport not in self._started:
            raise RuntimeError("New transports require a host restart")
        self._declared[transport] = self._declared.get(transport, 0) + 1

        def remove() -> None:
            remaining = self._declared.get(transport, 0) - 1
            if remaining > 0:
                self._declared[transport] = remaining
            else:
                self._declared.pop(transport, None)

        track_registration(remove, owner)

    async def startup(self, *entrances: ChannelTransport) -> None:
        """Open requested transports and roll back partial initialization."""
        if self._running:
            return
        self._running = True
        try:
            for transport in (*entrances, *self._declared):
                self._started.append(transport)
                await transport.startup()
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                try:
                    await self.shutdown()
                except BaseException as cleanup_error:
                    raise BaseExceptionGroup(
                        "Channel startup and cleanup failed",
                        [error, cleanup_error],
                    ) from None
            raise

    async def stopping(self) -> None:
        """Stop every entrance before draining accepted work."""
        errors: list[Exception] = []
        for transport in self._started:
            try:
                transport.stop_accepting()
            except Exception as error:
                errors.append(error)
        for transport in reversed(self._started):
            if not isinstance(transport, BufferedTransport):
                continue
            try:
                await transport.drain()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Channel drain failed", errors)

    async def shutdown(self) -> None:
        """Close all started transports even if one fails to close."""
        errors: list[BaseException] = []
        with anyio.CancelScope(shield=True):
            while self._started:
                transport = self._started.pop()
                try:
                    await transport.shutdown()
                except BaseException as error:
                    errors.append(error)
        self._running = False
        if errors:
            raise BaseExceptionGroup("Channel shutdown failed", errors)


transports = ChannelLifecycles()


@on_startup
async def startup() -> None:
    """Open the configured API entrance and declared robot transports."""
    from .api.channel import APIChannel

    entrance = APIChannel.from_config()
    await transports.startup(*(() if entrance is None else (entrance,)))


@on_stopping
async def stopping() -> None:
    """Drain entrances before dependent plugins release their resources."""
    await transports.stopping()


@on_shutdown
async def shutdown() -> None:
    """Release transports after their consumers have stopped."""
    await transports.shutdown()
