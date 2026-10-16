"""Worker command line: telemetry, model registration and graceful shutdown."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress

from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.telemetry import (
    configure_process_telemetry,
    flush_process_telemetry,
    shutdown_process_telemetry,
)
from opsmesh.bootstrap.worker import run_dispatch_worker
from opsmesh.config import get_settings
from opsmesh.telemetry.logging import configure_logging, shutdown_logging

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        format=settings.log_format,
        queue_capacity=settings.log_queue_capacity,
        service_name=settings.service_name,
        environment=settings.environment,
    )
    register_models()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if hasattr(loop, "add_signal_handler"):
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
            except NotImplementedError:
                break
    try:
        configure_process_telemetry(settings)
        await run_dispatch_worker(settings, stop)
    finally:
        flush_process_telemetry()
        shutdown_logging()
        shutdown_process_telemetry()


def main() -> None:
    parser = argparse.ArgumentParser(prog="opsmesh-worker")
    parser.parse_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
