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
        self._dirty: set[ChannelTransport] = set()
        self._running = False
        self._owners: dict[ChannelTransport, set[str]] = {}
        self._managed: set[ChannelTransport] = set()

    @property
    def has_declarations(self) -> bool:
        """Whether any robot transports have been declared by consumers."""
        return bool(self._declared)

    def use(self, transport: ChannelTransport, owner: Any = None) -> None:
        """Attach one declaration to the framework's owning plugin scope."""
        from opsmesh.drivers import get_driver
        from opsmesh.plugins.model import Plugin
        from opsmesh.plugins.registry import (
            _current_plugin,
            get_plugin_by_module_name,
        )

        plugin = _current_plugin.get()
        if plugin is None and owner is not None:
            plugin = get_plugin_by_module_name(getattr(owner, "__module__", ""))
        if isinstance(plugin, Plugin):
            self._managed.add(transport)
            driver = get_driver()

            async def prepare() -> None:
                self._check_clean(transport)
                owners = self._owners.setdefault(transport, set())
                owners.add(plugin.id_)
                await self._start_transport(transport)

            async def start() -> None:
                self._check_clean(transport)
                if (
                    getattr_static(transport, "start_accepting", None)
                    is not None
                ):
                    await cast(StreamingTransport, transport).start_accepting()

            async def close() -> None:
                owners = self._owners.get(transport, set())
                owners.discard(plugin.id_)
                if not owners and transport in self._started:
                    await self._close_transport(transport)

            driver.on_ready(prepare)
            driver.on_started(start)
            driver.on_shutdown(close)
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
        for transport in self._dirty:
            self._check_clean(transport)
        if self._running:
            return
        self._running = True
        try:
            for transport in (
                *entrances,
                *(item for item in self._declared if item not in self._managed),
            ):
                await self._start_transport(transport)
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
            self._check_clean(transport)
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
            for transport in reversed(tuple(self._started)):
                try:
                    await self._close_transport(transport)
                except BaseException as error:
                    errors.append(error)
        self._running = bool(self._started)
        if errors:
            raise BaseExceptionGroup("Channel shutdown failed", errors)

    def _check_clean(self, transport: ChannelTransport) -> None:
        if transport in self._dirty:
            raise RuntimeError("Channel transport requires successful cleanup")

    async def _start_transport(self, transport: ChannelTransport) -> None:
        self._check_clean(transport)
        if transport in self._started:
            return
        self._started.append(transport)
        self._dirty.add(transport)
        await transport.startup()
        self._dirty.discard(transport)

    async def _close_transport(self, transport: ChannelTransport) -> None:
        self._dirty.add(transport)
        await transport.shutdown()
        self._started.remove(transport)
        self._dirty.discard(transport)
