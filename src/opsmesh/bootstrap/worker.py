"""Assemble the durable dispatch Worker: native Client plus the Outbox consumer."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass

from opsmesh.bootstrap.resources import secret_cipher, temporal_database
from opsmesh.bootstrap.temporal import connect_temporal
from opsmesh.config import Settings
from opsmesh.telemetry.context import restore_trace_context
from opsmesh.temporal.dispatch.dispatcher import TemporalOutboxDispatcher
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher


@dataclass(frozen=True)
class DispatchWorker:
    """The S2 worker runs the transactional dispatch consumer.

    Feature Workflows and Activities register here from S4 onward; the Outbox
    consumer is the only durable component the base process requires.
    """

    dispatcher: TemporalOutboxDispatcher


async def build_dispatch_worker(settings: Settings, resources: AsyncExitStack) -> DispatchWorker:
    client = await connect_temporal(settings)
    database = await resources.enter_async_context(temporal_database(settings))
    publisher = TemporalOutboxPublisher(
        database.sessions,
        client,
        task_queue=settings.temporal_task_queue,
        concurrency=settings.temporal_dispatch_concurrency,
        cipher=secret_cipher(settings),
        restore_context=restore_trace_context,
    )
    dispatcher = TemporalOutboxDispatcher(
        publisher,
        database_url=settings.database_url,
    )
    return DispatchWorker(dispatcher=dispatcher)


async def run_dispatch_worker(settings: Settings, stop: asyncio.Event) -> None:
    async with AsyncExitStack() as resources:
        worker = await build_dispatch_worker(settings, resources)
        await worker.dispatcher.run(stop)
