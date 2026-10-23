"""Dedicated native gateway process; API replicas never own channel sockets."""

import asyncio
import signal
from contextlib import AsyncExitStack
from pathlib import Path

from opsmesh.bootstrap.connections import build_database_resources
from opsmesh.bootstrap.gateway import GatewayExecution
from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.plugins import create_plugin_manager, watch_plugins
from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.config import Settings
from opsmesh.gateway.channels.manager import Gateway
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.plugins.manager import PluginSnapshot
from opsmesh.resources.layout import StorageLayout
from opsmesh.telemetry.logging import configure_logging, shutdown_logging


async def run_gateway(settings: Settings) -> None:
    configure_logging(
        level=settings.log_level,
        format=settings.log_format,
        queue_capacity=settings.log_queue_capacity,
        service_name=settings.service_name,
        environment=settings.environment,
        redaction=PLATFORM_REDACTION,
    )
    register_models()
    database = build_database_resources(settings)
    async with AsyncExitStack() as resources:
        resources.callback(shutdown_logging)
        resources.callback(database.engine.dispose)
        layout = StorageLayout(Path(settings.storage_root), settings.plugins_root)
        plugins = create_plugin_manager(settings, database.cipher, role="gateway", layout=layout)
        resources.callback(plugins.close)
        await plugins.reload()
        gateway = Gateway(
            registry=plugins.current.views["channels"],
            store=GatewayStore(database.sessions, database.cipher),
            media_root=layout.gateway_media,
            execution=GatewayExecution(
                database.sessions, database.cipher, layout.history, layout.gateway_media
            ),
        )

        async def apply(snapshot: PluginSnapshot) -> None:
            await gateway.replace_registry(snapshot.views["channels"])

        plugins.apply = apply
        watch_plugins(plugins, resources)
        resources.push_async_callback(gateway.stop)
        await gateway.start()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stopped.set)
            except NotImplementedError:
                continue
            resources.callback(loop.remove_signal_handler, signum)
        await stopped.wait()
