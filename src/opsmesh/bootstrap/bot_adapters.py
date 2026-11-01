"""Built-in bot implementations assembled at the platform composition boundary."""

from collections.abc import Callable

import httpx

from opsmesh.framework.adapters import BotAdapterRegistry
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.infrastructure.bots.dingtalk import DingTalkAdapter
from opsmesh.infrastructure.bots.qq import QQAdapter


def create_bot_adapters(
    driver: OpsMeshDriver,
    client: httpx.AsyncClient,
    resolve_secret: Callable[[str], str],
    *,
    autostart: bool,
) -> BotAdapterRegistry:
    registry = BotAdapterRegistry(driver)
    for adapter_type in (DingTalkAdapter, QQAdapter):
        if adapter_type.name not in driver.adapters:
            registry.register(adapter_type(driver, client, resolve_secret, autostart=autostart))
    return registry
