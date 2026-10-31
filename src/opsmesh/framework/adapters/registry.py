"""Typed access to bot implementations registered with the application Driver."""

import asyncio
from collections.abc import Iterable

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError

from .configuration import BotAdapterDescriptor
from .managed import BotEventConsumer, OpsMeshBotAdapter


class BotAdapterRegistry:
    def __init__(self, driver: OpsMeshDriver) -> None:
        self.driver = driver
        self._consumer: BotEventConsumer | None = None
        self._closed_disabled: set[OpsMeshBotAdapter] = set()
        self._pending_reconfiguration: set[OpsMeshBotAdapter] = set()
        self._policy_lock = asyncio.Lock()

    def register(self, adapter: OpsMeshBotAdapter) -> None:
        self.driver.register_adapter(adapter)
        adapter.event_consumer = self._consumer

    def get(self, name: str) -> OpsMeshBotAdapter:
        adapter = self.driver.adapters.get(name)
        if not isinstance(adapter, OpsMeshBotAdapter):
            raise OpsMeshError("BOT_ADAPTER_UNAVAILABLE", "Bot adapter is not registered")
        adapter.ensure_enabled()
        adapter.event_consumer = self._consumer
        return adapter

    def values(self, *, include_disabled: bool = False) -> tuple[OpsMeshBotAdapter, ...]:
        return tuple(
            value
            for value in self.driver.adapters.values()
            if isinstance(value, OpsMeshBotAdapter) and (include_disabled or value.enabled)
        )

    def describe(self) -> tuple[BotAdapterDescriptor, ...]:
        return tuple(adapter.describe() for adapter in self.values())

    def bind_receiver(self, consumer: BotEventConsumer) -> None:
        self._consumer = consumer
        for adapter in self.values(include_disabled=True):
            adapter.event_consumer = consumer

    async def synchronize(self) -> tuple[str, ...]:
        """Drain disabled implementations and return names requiring reconfiguration.

        The platform owns account persistence and acknowledges successful reconfiguration.
        Pending names survive both other shutdown failures and platform configuration errors.
        """
        errors: list[Exception] = []
        async with self._policy_lock:
            current = set(self.values(include_disabled=True))
            self._closed_disabled.intersection_update(current)
            self._pending_reconfiguration.intersection_update(current)
            for adapter in current:
                if adapter.enabled:
                    if adapter in self._closed_disabled:
                        self._closed_disabled.remove(adapter)
                        self._pending_reconfiguration.add(adapter)
                    continue
                self._pending_reconfiguration.discard(adapter)
                if adapter in self._closed_disabled:
                    continue
                try:
                    await adapter.close()
                except Exception as error:
                    errors.append(error)
                    continue
                if adapter.enabled:
                    # A policy update can re-enable the plugin while close is draining.
                    self._pending_reconfiguration.add(adapter)
                else:
                    self._closed_disabled.add(adapter)
        if errors:
            raise ExceptionGroup("Disabled bot adapter shutdown failed", errors)
        return tuple(
            sorted(adapter.name for adapter in self._pending_reconfiguration if adapter.enabled)
        )

    def acknowledge_reconfigured(self, names: Iterable[str]) -> None:
        """Clear pending activation only after every account for that adapter was configured."""
        selected = set(names)
        self._pending_reconfiguration.difference_update(
            adapter
            for adapter in tuple(self._pending_reconfiguration)
            if adapter.name in selected and adapter.enabled
        )

    async def close(self) -> None:
        errors: list[Exception] = []
        for adapter in reversed(self.values(include_disabled=True)):
            try:
                await adapter.close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Bot adapter shutdown failed", errors)
