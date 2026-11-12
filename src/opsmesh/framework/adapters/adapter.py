"""Adapter registration and transport delegation."""

import abc
from typing import Any

from opsmesh.framework.config import Config
from opsmesh.framework.drivers import (
    ASGIMixin,
    Driver,
    HTTPClientMixin,
    HTTPServerSetup,
    Request,
    Response,
    WebSocketServerSetup,
)
from opsmesh.framework.lifecycle import LIFESPAN_FUNC

from .channel import Channel


class Adapter(abc.ABC):
    """Base interface for protocol adapters.

    Adapters establish connections and translate incoming and outgoing data.

    Args:
        driver: The framework driver instance.
        kwargs: Extra arguments supplied by Driver.register_adapter.
    """

    def __init__(self, driver: Driver, **kwargs: Any):
        self.driver: Driver = driver
        """The framework driver instance."""
        self.channels: dict[str, Channel] = {}
        """Connected channel instances managed by this adapter."""

    def __repr__(self) -> str:
        return f"Adapter(name={self.get_name()!r})"

    @classmethod
    @abc.abstractmethod
    def get_name(cls) -> str:
        """The protocol adapter name."""
        raise NotImplementedError

    @property
    def config(self) -> Config:
        """The global framework configuration."""
        return self.driver.config

    def connect(self, channel: Channel) -> None:
        """Register a newly connected channel with the framework.

        Call this after establishing a channel connection successfully.

        Args:
            channel: The channel instance.
        """
        self.driver.connect(channel)
        self.channels[channel.self_id] = channel

    def disconnect(self, channel: Channel) -> None:
        """Unregister a disconnected channel from the framework.

        Call this after closing the channel connection.

        Args:
            channel: The channel instance.
        """
        if self.channels.pop(channel.self_id, None) is None:
            raise RuntimeError(
                f"{channel} not found in adapter {self.get_name()}"
            )
        self.driver.disconnect(channel)

    def setup_http_server(self, setup: HTTPServerSetup):
        """Register an HTTP server route."""
        if not isinstance(self.driver, ASGIMixin):
            raise TypeError("Current driver does not support http server")
        self.driver.setup_http_server(setup)

    def setup_websocket_server(self, setup: WebSocketServerSetup):
        """Register a WebSocket server route."""
        if not isinstance(self.driver, ASGIMixin):
            raise TypeError("Current driver does not support websocket server")
        self.driver.setup_websocket_server(setup)

    async def request(self, setup: Request) -> Response:
        """Send an HTTP client request."""
        if not isinstance(self.driver, HTTPClientMixin):
            raise TypeError("Current driver does not support http client")
        return await self.driver.request(setup)

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback for the driver readiness phase."""
        return self.driver.on_ready(func)

    @abc.abstractmethod
    async def _call(self, channel: Channel, operation: str, **data: Any) -> Any:
        """Implement the protocol-specific operation call.

        Args:
            channel: The channel issuing the operation.
            operation: The operation name.
            data: Operation arguments.
        """
        raise NotImplementedError


__autodoc__ = {"Adapter._call": True}
