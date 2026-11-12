"""Logical channel registration and protocol operation dispatch."""

import abc
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from opsmesh.config import Config
from opsmesh.drivers import Driver
from opsmesh.lifecycle import LIFESPAN_FUNC

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
        """The framework runtime instance."""

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

    @property
    def channels(self) -> Mapping[str, Channel]:
        """Read the driver's connections owned by this adapter."""
        return MappingProxyType(
            {
                key: channel
                for key, channel in self.driver.channels.items()
                if channel.adapter is self
            }
        )

    def connect(self, channel: Channel) -> None:
        """Register a newly connected channel with the framework.

        Call this after establishing a channel connection successfully.

        Args:
            channel: The channel instance.
        """
        self.driver.connect(channel)

    def disconnect(self, channel: Channel) -> None:
        """Unregister a disconnected channel from the framework.

        Call this after closing the channel connection.

        Args:
            channel: The channel instance.
        """
        if self.channels.get(channel.self_id) is not channel:
            raise RuntimeError(
                f"{channel} not found in adapter {self.get_name()}"
            )
        self.driver.disconnect(channel)

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback for the runtime readiness phase."""
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
