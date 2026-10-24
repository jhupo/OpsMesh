"""Dedicated native gateway process; API replicas never own channel sockets."""

import asyncio
import signal
from contextlib import AsyncExitStack
from pathlib import Path

from opsmesh.agents.catalog.service import ManagedCatalog
from opsmesh.bootstrap.connections import build_database_resources
from opsmesh.bootstrap.gateway import GatewayExecution
from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.plugins import create_plugin_manager, watch_plugins
from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.config import Settings
from opsmesh.gateway.channels.manager import Gateway
from opsmesh.gateway.management.contracts import resolve_generation
from opsmesh.gateway.management.events import ConversationEvents
from opsmesh.gateway.management.worker import ManagementWorker
from opsmesh.gateway.messaging.filters import MessageFilters
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
        execution = GatewayExecution(
            database.sessions, database.cipher, layout.history, layout.gateway_media
        )
        managed = ManagedCatalog(database.sessions, database.cipher)
        store = GatewayStore(database.sessions, database.cipher)
        gateway = Gateway(
            registry=plugins.current.views["channels"],
            store=store,
            media_root=layout.gateway_media,
            execution=execution,
            management=ManagementWorker(
                store,
                execution.management_actor,
                lambda identifier: resolve_generation(plugins.current.resources, identifier),
                lambda kind: plugins.current.views["channels"].generation(kind),
            ),
            events=ConversationEvents(
                store,
                lambda: plugins.current.resources,
                managed.credential,
                execution.management_actor,
                lambda kind: plugins.current.views["channels"].generation(kind),
            ),
            filters=MessageFilters(
                lambda: plugins.current.resources,
                managed.credential,
                execution.filter_actor,
                layout.gateway_media,
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
