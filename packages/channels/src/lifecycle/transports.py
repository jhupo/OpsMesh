"""Track consumer-owned bot adapters and coordinate their lifecycle."""

from inspect import getattr_static
from typing import Any, Protocol, cast

import anyio

from opsmesh.plugins.registry import track_registration


class ChannelTransport(Protocol):
    """Lifecycle shared by adapters and the API entrance owner."""

    async def startup(self) -> None:
        """Acquire resources before consumers become ready."""

    def stop_accepting(self) -> None:
        """Reject new requests while keeping reply resources available."""

    async def shutdown(self) -> None:
        """Release resources, including partially initialized ones."""


class BufferedTransport(ChannelTransport, Protocol):
    """A transport that owns work beyond the HTTP request lifetime."""

    async def drain(self) -> None:
        """Complete or cancel accepted work before consumers close."""


class StreamingTransport(ChannelTransport, Protocol):
    """A transport whose inbound connections open after consumer readiness."""

    async def start_accepting(self) -> None:
        """Start receiving from a remote service."""


class ChannelTransports:
    """Retain declared transports until their consumers finish using them."""

    def __init__(self) -> None:
        """Create a stopped coordinator without protocol dependencies."""
        self._declared: dict[ChannelTransport, int] = {}
        self._started: list[ChannelTransport] = []
        self._running = False

    @property
    def has_declarations(self) -> bool:
        """Whether any robot transports have been declared by consumers."""
        return bool(self._declared)

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

    async def start_accepting(self) -> None:
        """Open remote inputs after all consumers have initialized."""
        for transport in self._started:
            if getattr_static(transport, "start_accepting", None) is not None:
                await cast(StreamingTransport, transport).start_accepting()

    async def stopping(self) -> None:
        """Stop every entrance before draining accepted work."""
        errors: list[Exception] = []
        for transport in self._started:
            try:
                transport.stop_accepting()
            except Exception as error:
                errors.append(error)
        for transport in reversed(self._started):
            # Channel.__getattr__ exposes RPC methods, not lifecycle hooks.
            if getattr_static(transport, "drain", None) is None:
                continue
            try:
                await cast(BufferedTransport, transport).drain()
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
