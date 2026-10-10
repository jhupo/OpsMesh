from __future__ import annotations

import argparse
import logging
import signal
import socket
from threading import Event

from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.runtime import get_default_docker_runtime_client
from opsmesh.bootstrap.telemetry import configure_worker_telemetry
from opsmesh.bootstrap.worker import build_worker_runner
from opsmesh.orchestration.runs.service import build_default_queue
from opsmesh.runtime.workers.models import WorkerRunnerConfig
from opsmesh.runtime.workers.runner import WorkerRunner
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import SessionLocal, engine
from opsmesh.shared.redis.client import redis_client
from opsmesh.shared.telemetry.logging import configure_logging

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    telemetry_runtime = configure_worker_telemetry(settings, engine=engine)
    try:
        return _run_worker(args, settings)
    finally:
        telemetry_runtime.shutdown()


def _run_worker(args: argparse.Namespace, settings: Settings) -> int:

    stop_event = Event()
    _install_signal_handlers(stop_event)

    config = WorkerRunnerConfig(
        worker_id=args.worker_id or _default_worker_id(),
        worker_type=args.worker_type,
        concurrency=args.concurrency,
        blocking_io_concurrency=args.blocking_io_concurrency,
        queue_name=args.queue_name or settings.worker_queue_name,
        heartbeat_interval_seconds=args.heartbeat_interval_seconds,
        idle_sleep_seconds=args.idle_sleep_seconds,
        maintenance_interval_seconds=args.maintenance_interval_seconds,
        run_lease_seconds=args.run_lease_seconds,
    )
    runner = _build_runner(settings, config)

    logger.info(
        "Starting worker",
        extra={"worker_id": config.worker_id, "queue": config.queue_name},
    )
    if args.once:
        handled = runner.run_once()
        runner.record_heartbeat(
            "online",
            {"processed": int(handled), "idle_polls": int(not handled)},
        )
        return 0

    summary = runner.run(max_jobs=args.max_jobs, stop_event=stop_event)
    logger.info(
        "Worker stopped",
        extra={
            "worker_id": config.worker_id,
            "processed": summary.processed,
            "idle_polls": summary.idle_polls,
            "stopped": summary.stopped,
        },
    )
    return 0


def _build_runner(settings: Settings, config: WorkerRunnerConfig) -> WorkerRunner:
    register_models()
    return build_worker_runner(
        queue=build_default_queue(redis_client, settings),
        session_factory=SessionLocal,
        config=config,
        settings=settings,
        runtime_docker_client=get_default_docker_runtime_client(),
    )


def _install_signal_handlers(stop_event: Event) -> None:
    def request_stop(signum: int, _: object) -> None:
        logger.info("Worker shutdown requested", extra={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def _default_worker_id() -> str:
    return f"worker-{socket.gethostname()}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an OpsMesh background worker.")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--worker-type", default="cloud")
    parser.add_argument("--queue-name", default=None)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--blocking-io-concurrency", type=int, default=8)
    parser.add_argument("--once", action="store_true", help="Consume at most one job and exit.")
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Stop after processing this many jobs.",
    )
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=30.0)
    parser.add_argument("--idle-sleep-seconds", type=float, default=1.0)
    parser.add_argument("--maintenance-interval-seconds", type=float, default=60.0)
    parser.add_argument("--run-lease-seconds", type=int, default=900)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
