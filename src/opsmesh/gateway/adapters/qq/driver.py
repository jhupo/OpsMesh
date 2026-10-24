"""Embed NoneBot's native transport without its process-global bot configuration."""

import asyncio
import logging
from typing import Any, cast

from nonebot.adapters import Adapter as BaseAdapter
from nonebot.adapters import Bot as BaseBot
from nonebot.adapters import Event as BaseEvent
from nonebot.adapters.qq import Adapter
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.config import Config as NativeQQConfig
from nonebot.adapters.qq.event import Event as QQEvent
from nonebot.config import Config, Env
from nonebot.drivers import Driver, combine_driver
from nonebot.drivers.fastapi import Driver as FastAPIDriver
from nonebot.drivers.httpx import Mixin as HTTPXMixin
from nonebot.drivers.websockets import Mixin as WebSocketMixin
from nonebot.exception import IgnoredException
from nonebot.log import logger as native_logger
from nonebot.message import event_preprocessor
from opsmesh_sdk.channels import EventSink

from .config import QQConfig
from .events import normalize
from .webhook import WebhookGuard

logger = logging.getLogger(__name__)
# Native debug/event logs contain message bodies and credentials. Host logs contain codes only.
native_logger.disable("nonebot")


@event_preprocessor
async def route_event(bot: BaseBot, event: BaseEvent) -> None:
    if isinstance(bot.adapter, HostedAdapter) and isinstance(event, QQEvent):
        try:
            await bot.adapter.sink(normalize(event))
        except Exception:
            logger.exception("qq_event_persistence_failed")
            raise
        raise IgnoredException("Handled by OpsMesh")


class HostedAdapter(Adapter):
    def __init__(
        self, driver: Driver, config: NativeQQConfig, sink: EventSink, ready: asyncio.Event
    ):
        # Upstream's constructor reads get_plugin_config() from a single global driver.
        # Only constructor wiring differs; native setup/transport/parsing/API code is retained.
        BaseAdapter.__init__(self, driver)
        self.qq_config = config
        self.tasks: set[asyncio.Task[Any]] = set()
        self.sink, self.ready = sink, ready
        self.setup()

    def bot_connect(self, bot: BaseBot) -> None:
        super().bot_connect(bot)
        self.ready.set()
        logger.info("qq_connected")

    def bot_disconnect(self, bot: BaseBot) -> None:
        super().bot_disconnect(bot)
        if not self.bots:
            self.ready.clear()
        logger.info("qq_disconnected")


def build_driver(
    config: QQConfig, sink: EventSink, ready: asyncio.Event
) -> tuple[FastAPIDriver, HostedAdapter]:
    driver_type = combine_driver(FastAPIDriver, HTTPXMixin, WebSocketMixin)
    driver = cast(
        FastAPIDriver,
        driver_type(
            Env(_env_file=None), Config(_env_file=None, driver="~fastapi+~httpx+~websockets")
        ),
    )
    driver.server_app.add_middleware(WebhookGuard)
    native_config = NativeQQConfig(
        qq_verify_webhook=True,
        qq_bots=[
            BotInfo(
                id=config.app_id,
                secret=config.secret.get_secret_value(),
                intent=config.intents,
                shard=config.shard,
                use_websocket=config.transport == "websocket",
            )
        ],
    )
    return driver, HostedAdapter(driver, native_config, sink, ready)
