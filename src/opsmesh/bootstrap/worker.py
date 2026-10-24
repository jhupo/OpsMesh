import argparse
import asyncio
import logging
import signal

from opsmesh.gateway.worker import GatewayWorker
from opsmesh.infra.capacity import worker_concurrency
from opsmesh.quotas.config import ModelDefaults
from opsmesh.tasks.runtime.leases import recover_expired
from opsmesh.tasks.runtime.runner import TaskRunner
from opsmesh.tasks.scheduler import serve as serve_scheduler

from .app import Application

logger = logging.getLogger(__name__)


async def task_worker(application: Application) -> None:
    settings = application.settings
    concurrency = worker_concurrency(settings.worker_concurrency)
    runner = TaskRunner(
        application.sessions,
        application.cipher,
        application.registry,
        settings.data_dir,
        lease_seconds=settings.lease_seconds,
        model_defaults=ModelDefaults(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key.get_secret_value() if settings.model_api_key else None,
            name=settings.model_name,
            api_mode=settings.model_api_mode,
            reasoning_effort=settings.model_reasoning_effort,
        ),
        encryption_key=settings.encryption_key.get_secret_value(),
        session_ttl_seconds=settings.session_ttl_seconds,
        sandbox_root=settings.sandbox_root,
        child_concurrency=concurrency,
    )
    running: set[asyncio.Task[None]] = set()
    try:
        while True:
            for completed in list(running):
                if completed.done():
                    running.remove(completed)
                    if not completed.cancelled():
                        try:
                            completed.result()
                        except Exception:
                            logger.exception("task_worker_execution_failed")
            try:
                recover_expired(application.sessions, application.cipher)
                while len(running) < concurrency:
                    claimed = runner.leases.claim()
                    if claimed is None:
                        break
                    running.add(asyncio.create_task(runner.execute(*claimed)))
            except Exception:
                logger.exception("task_worker_poll_failed")
            await asyncio.sleep(0.5)
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


async def serve(kind: str) -> None:
    application = Application.create()
    current = asyncio.current_task()
    assert current is not None
    loop = asyncio.get_running_loop()
    handled = [signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        handled.append(signal.SIGBREAK)
    previous = {kind: signal.getsignal(kind) for kind in handled}
    for kind_signal in handled:
        signal.signal(kind_signal, lambda *_: loop.call_soon_threadsafe(current.cancel))
    try:
        if kind == "tasks":
            await task_worker(application)
        elif kind == "scheduler":
            await serve_scheduler(application.sessions, application.cipher)
        elif kind == "gateway":
            await GatewayWorker(
                application.sessions,
                application.cipher,
                application.registry,
                application.settings.data_dir,
                application.settings.lease_seconds,
            ).serve()
        else:
            raise ValueError("Unknown worker type")
    finally:
        try:
            await application.close()
        finally:
            for kind_signal, handler in previous.items():
                signal.signal(kind_signal, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsMesh worker")
    parser.add_argument("kind", choices=["tasks", "scheduler", "gateway"])
    arguments = parser.parse_args()
    try:
        asyncio.run(serve(arguments.kind))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
