"""Run a durable Temporal Worker and its transactional dispatch publisher."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, closing

from opsmesh.agents.sessions.activities import SessionActivities
from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.telemetry import configure_worker_telemetry
from opsmesh.platform.maintenance.schedules import ensure_platform_schedules
from opsmesh.resources.storage.backends import create_storage
from opsmesh.resources.transfers.worker import build_archive_temporal_worker
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.temporal.client import TemporalExecutionClient, connect_temporal
from opsmesh.runtime.temporal.database import temporal_database
from opsmesh.runtime.temporal.dispatcher import TemporalOutboxDispatcher
from opsmesh.runtime.temporal.outbox import TemporalOutboxPublisher
from opsmesh.runtime.temporal.worker import build_temporal_worker
from opsmesh.shared.config import get_settings
from opsmesh.shared.db.session import SessionLocal, engine
from opsmesh.shared.telemetry.logging import configure_logging

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings)
    register_models()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if hasattr(loop, "add_signal_handler"):
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
            except NotImplementedError:
                break
    async with AsyncExitStack() as resources:
        telemetry = configure_worker_telemetry(settings, engine=engine)
        resources.callback(telemetry.shutdown)
        resources.callback(engine.dispose)
        client = await connect_temporal(settings)
        await ensure_platform_schedules(client, settings)
        execution_client = TemporalExecutionClient(
            client,
            task_queue=settings.temporal_task_queue,
        )
        database = await resources.enter_async_context(temporal_database(settings))
        session_activities = SessionActivities(database.engine, SessionLocal)
        resources.push_async_callback(session_activities.close)
        sandbox_backends = resources.enter_context(build_sandbox_backends(settings))
        storage = resources.enter_context(closing(create_storage(settings)))
        executor = resources.enter_context(
            ThreadPoolExecutor(
                max_workers=settings.temporal_blocking_activity_workers,
                thread_name_prefix="opsmesh-temporal-activity",
            )
        )
        worker = build_temporal_worker(
            client,
            settings=settings,
            session_activities=session_activities,
            activity_executor=executor,
            sandbox_backends=sandbox_backends,
            storage=storage,
        )
        archive_executor = resources.enter_context(
            ThreadPoolExecutor(
                max_workers=settings.temporal_archive_activity_concurrency,
                thread_name_prefix="opsmesh-archive-activity",
            )
        )
        archive_worker = build_archive_temporal_worker(
            client,
            settings=settings,
            activity_executor=archive_executor,
        )
        dispatcher = TemporalOutboxDispatcher(
            TemporalOutboxPublisher(
                database.sessions,
                execution_client,
                concurrency=settings.temporal_dispatch_concurrency,
            ),
            database_url=settings.database_url,
        )
        worker_task = asyncio.create_task(worker.run(), name="temporal-worker")
        archive_worker_task = asyncio.create_task(
            archive_worker.run(), name="temporal-archive-worker"
        )
        dispatcher_task = asyncio.create_task(
            dispatcher.run(stop), name="temporal-outbox-dispatcher"
        )
        stopping = asyncio.create_task(stop.wait(), name="temporal-worker-stop")
        try:
            done, _ = await asyncio.wait(
                (worker_task, archive_worker_task, dispatcher_task, stopping),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
        finally:
            failure = sys.exception()
            stop.set()
            dispatcher_task.cancel()
            stopping.cancel()
            shutdown_results = await asyncio.gather(
                worker.shutdown(),
                archive_worker.shutdown(),
                return_exceptions=True,
            )
            for task, result in zip(
                (worker_task, archive_worker_task),
                shutdown_results,
                strict=True,
            ):
                if isinstance(result, BaseException):
                    task.cancel()
            task_results = await asyncio.gather(
                dispatcher_task,
                worker_task,
                archive_worker_task,
                stopping,
                return_exceptions=True,
            )
            cleanup_errors = [
                result
                for result in [*shutdown_results, *task_results]
                if isinstance(result, Exception) and result is not failure
            ]
            if cleanup_errors:
                if failure is None:
                    raise ExceptionGroup("Temporal Worker shutdown failed", cleanup_errors)
                for error in cleanup_errors:
                    logger.error(
                        "Worker cleanup failed after execution failure",
                        extra={"exception_type": type(error).__name__},
                    )


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
